from urllib.parse import quote_plus
from sqlalchemy.orm import sessionmaker
from pandas.io import sql

import sqlalchemy as mysql
import pandas as pd
import xml.etree.ElementTree as ET
import os, pyodbc


class XMLParseClass:
    def __init__(self, file):
        try:
            tree = ET.parse(file)
            self.root = tree.getroot()
        except AssertionError as a:
            print('\t[-] {} : Parse failed.'.format(a))
            pass

    def parseelement(self, element, parsed=None):
        if parsed is None:
            parsed = dict()

        if element.keys():
            for key in element.keys():
                if key not in parsed:
                    parsed[key] = element.attrib.get(key)

                if element.text and element.tag not in parsed:
                    parsed[element.tag] = element.text

        elif element.text and element.tag not in parsed:
            parsed[element.tag] = element.text

        for child in list(element):
            self.parseelement(child, parsed)
        return parsed

    def parsexml(self, findpath, dictvar=None):
        if isinstance(dictvar, dict):
            for item in self.root.findall(findpath):
                dictvar = self.parseelement(item, dictvar)

            return dictvar
        else:
            parsed = [self.parseelement(item) for item in self.root.findall(findpath)]
            df = pd.DataFrame(parsed)

            return df.applymap(lambda x: x.strip() if isinstance(x, str) else x)


class SQLConnect:
    session = False
    engine = None
    conn = None
    cursor = None

    def __init__(self,conn_type,dsn=None):
        self.conn_type = conn_type

        if conn_type == 'alch':
            self.connstring = self.alchconnstr(
                '{SQL Server Native Client 11.0}', 1433, settings['Server'], settings['Database'], 'mssql'
                )
        elif conn_type == 'sql':
            self.connstring = self.sqlconnstr(settings['Server'], settings['Database'])
        elif conn_type == 'dsn':
            self.connstring = self.dsnconnstr(dsn)

    @staticmethod
    def alchconnstr(driver, port, server, database, flavor='mssql'):
        p = quote_plus(
                'DRIVER={};PORT={};SERVER={};DATABASE={};Trusted_Connection=yes;'
                .format(driver, port, server, database))

        return '{}+pyodbc:///?odbc_connect={}'.format(flavor, p)

    @staticmethod
    def sqlconnstr(server, database):
        return 'driver={0};server={1};database={2};autocommit=True;Trusted_Connection=yes'.format('{SQL Server}',
                                                                                                  server, database)

    @staticmethod
    def dsnconnstr(dsn):
        return 'DSN={};DATABASE=default;Trusted_Connection=Yes;'.format(dsn)

    def connect(self):
        if self.conn_type == 'alch':
            self.engine = mysql.create_engine(self.connstring)
        else:
            self.conn = pyodbc.connect(self.connstring)
            self.cursor = self.conn.cursor()
            self.conn.commit()

    def close(self):
        if self.conn_type == 'alch':
            self.engine.dispose()
        else:
            self.cursor.close()
            self.conn.close()

    def createsession(self):
        if self.conn_type == 'alch':
            self.engine = sessionmaker(bind=self.engine)
            self.engine = self.engine()
            self.engine._model_changes = {}
            self.session = True

    def createtable(self, dataframe, sqltable):
        if self.conn_type == 'alch' and not self.session:
            dataframe.to_sql(
                sqltable,
                self.engine,
                if_exists='replace',
            )

    def upload(self, dataframe, sqltable):
        if self.conn_type == 'alch' and not self.session:
            mytbl = sqltable.split(".")

            if len(mytbl) > 1:
                dataframe.to_sql(
                    mytbl[1],
                    self.engine,
                    schema=mytbl[0],
                    if_exists='append',
                    index=True,
                    index_label='linenumber',
                    chunksize=1000
                )
            else:
                dataframe.to_sql(
                    mytbl[0],
                    self.engine,
                    if_exists='replace',
                    index=False,
                    chunksize=1000
                )

    def query(self, query):
        try:
            if self.conn_type == 'alch':
                obj = self.engine.execute(mysql.text(query))

                if obj._saved_cursor.arraysize > 0:
                    data = obj.fetchall()
                    columns = obj._metadata.keys

                    return pd.DataFrame(data, columns=columns)

            else:
                df = sql.read_sql(query, self.conn)
                return df

        except ValueError as a:
            print('\t[-] {} : SQL Query failed.'.format(a))
            pass

    def execute(self, query):
        try:
            if self.conn_type == 'alch':
                self.engine.execute(query)
            else:
                self.cursor.execute(query)

        except ValueError as a:
            print('\t[-] {} : SQL Execute failed.'.format(a))
            pass


def init():
    global settings
    global errors


def load_settings():
    mysettings = dict()
    updatesdir = []

    mysettings['SourceDir'] = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    mysettings['SourceCodeDir'] = mysettings['SourceDir'] + "\\03_Source_Code"

    updatesdir.append(mysettings['SourceDir'] + "\\01_Updates\\01_BMI-PCI")
    updatesdir.append(mysettings['SourceDir'] + "\\01_Updates\\02_Seeds")
    updatesdir.append(mysettings['SourceDir'] + "\\01_Updates\\03_Non-Seeds")
    updatesdir.append(mysettings['SourceDir'] + "\\01_Updates\\04_Dispute-Actions")
    updatesdir.append(mysettings['SourceDir'] + "\\01_Updates\\05_New-User")

    mysettings['UpdatesDir'] = updatesdir

    xmlobj = XMLParseClass(mysettings['SourceCodeDir'] + "\\Vacuum_Settings.xml")

    if xmlobj:
        mysettings = xmlobj.parsexml('./Settings/Network/', mysettings)
        mysettings = xmlobj.parsexml('./Settings/Read_Write_TBL/', mysettings)
        mysettings = xmlobj.parsexml('./Settings/Read_TBL/', mysettings)

        mysettings['Seed-Cost_Type'] = \
            xmlobj.parsexml('./Settings/CAT_Workbook/Seed_Disputes/', mysettings)['Cost_Type']

        df = xmlobj.parsexml('./Settings/CAT_Workbook/BMIPCI_Review/Action/')
        mysettings['BMIPCI-Action'] = df.loc[:, 'Action'].values

        df = xmlobj.parsexml('./Settings/CAT_Workbook/Dispute_Actions/Action/')
        mysettings['Dispute_Actions-Action'] = df.loc[:, 'Action'].values
        return mysettings
    else:
        raise ValueError("Unable to load Vacuum_Settings.xml. Please check path and file {0}"
                         .format(mysettings['SourceCodeDir']))


def append_errors(df):
    if not df.empty:
        errors.append(df)


def get_errors():
    if errors:
        return pd.concat(errors, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)


errors = []
settings = load_settings()
