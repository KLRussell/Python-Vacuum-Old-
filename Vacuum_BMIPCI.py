from Vacuum_Global import settings
from Vacuum_Global import SQLConnect
from Vacuum_Global import append_errors

import pandas as pd, datetime, os, time
import pathlib as pl


class BMIPCI:
    def __init__(self, action, df, upload_date):
        self.action = action
        self.df = df
        self.upload_date = upload_date

    @staticmethod
    def getbatch(asdate=False, dayofweek=4, weekoffset=-1):
        current_time = datetime.datetime.now()
        batch = (current_time.date() - datetime.timedelta(days=current_time.weekday()) +
                 datetime.timedelta(days=dayofweek, weeks=weekoffset))
        if asdate:
            return batch
        else:
            return batch.__format__("%Y%m%d")

    @staticmethod
    def update_map(asql, source_tbl, bmb_tbl, bmm_tbl, unmapped_tbl):
            param = None

            if source_tbl == 'BMI':
                param = ", C.frameID=B.initials"

            asql.execute('''
                insert into {1}
                (
                    gs_srvID,
                    gs_srvType,
                    {0}_ID,
                    Rep
                )
                select
                    A.gs_srvID,
                    A.gs_srvType,
                    A.Source_ID,
                    B.initials

                from mytbl2 As A
                inner join {2} As B
                on
                    A.Comp_Serial = B.Comp_Serial

                where
                    A.BMB = 1
                        and
                    A.Error_Columns is null
                '''.format(source_tbl, bmb_tbl, settings['CAT_Emp']))

            asql.execute('''
                update C
                    set
                        C.gs_SrvID=iif(A.BMB=1,A.Source_ID,A.gs_SrvID),
                        C.gs_SrvType=iif(A.BMB=1,iif(Source_TBL='PCI','M2M','BMB'),A.gs_SrvType),
                        C.Edit_Date=getdate()
                        {3}

                from mytbl2 As A
                inner join {2} As B
                on
                    A.Comp_Serial = B.Comp_Serial
                inner join {1} As C
                on
                    A.Source_ID = C.{0}_ID

                where
                    A.Error_Columns is null
                '''.format(source_tbl, bmm_tbl, settings['CAT_Emp'], param))

            asql.execute('''
                insert into {1}
                (
                    {0}_ID,
                    Audit_Result,
                    Rep,
                    Comment,
                    Edit_Date
                )
                select
                    A.Source_ID,
                    'Mapped',
                    B.initials,
                    A.action_comment,
                    getdate()

                from mytbl2 As A
                inner join {2} As B
                on
                    A.Comp_Serial = B.Comp_Serial

                where
                    A.Error_Columns is null
            '''.format(source_tbl, unmapped_tbl, settings['CAT_Emp']))

    @staticmethod
    def updatezerorev(asql, source, audit_result, comment, dispute=False):
        if source == 'BMI':
            zerorevtbl = settings['ZeroRevenue']
        else:
            zerorevtbl = settings['PCI_ZeroRevenue']

        if dispute:
            asql.upload(asql.query('''
                select distinct
                    Source_ID,
                    Action_Comment

                from mytbl2

                where
                    Source_TBL = '{0}'
            '''.format(source)), 'mydata')
        else:
            asql.upload(asql.query('''
                select
                    *
                from mytbl

                where
                    Source_TBL = '{0}'
            '''.format(source)), 'mydata')

        if asql.query("select object_id('mydata')").iloc[0, 0]:
            asql.execute('''
                INSERT INTO {0}
                (
                    {1}_ID,
                    Invoice_Date,
                    Tag,
                    Audit_Result,
                    Rep,
                    Comment,
                    Edit_Date
                )
                select
                    A.Source_ID,
                    B.Max_CNR_Date,
                    B.Tag,
                    '{4}',
                    C.Initials,
                    A.{5},
                    getdate()

                from mydata As A
                inner join {2} As B
                on
                    B.Source_TBL = '{1}'
                        and
                    A.Source_ID = B.Source_ID
                inner join {3} As C
                on
                    A.Comp_Serial = C.Comp_Serial
            '''.format(zerorevtbl, source, settings['CNR'], settings['CAT_Emp'], audit_result, comment))

            asql.execute('DROP TABLE mydata')

    @staticmethod
    def dispute_seeds(asql, data, tbl, seed, on, where, cost_type, record_type, inner=""):

        cols = "A." + ", A.".join(data.loc[:, ~data.columns.isin(['Action', 'Start_Date'])].columns.values)
        cols2 = ",".join(data.loc[:, ~data.columns.isin(['Action', 'Start_Date'])].columns.values)

        myquery = '''
            select
                {3} As Seed,
                '{5}' As Cost_Type,
                {6} As Record_Type,
                {4}

            from mytbl As A
            {7}
            inner join {0} As B
            on
                {1}
                    and
                {2}'''.format(tbl, on, where, seed, cols, cost_type, record_type, inner)

        if not asql.query("select object_id('mytbl2')").iloc[0, 0]:
            myresults = asql.query(myquery)

            if not myresults.empty:
                asql.upload(myresults, 'mytbl2')
        else:
            asql.execute('''
                insert into mytbl2
                (
                    Seed,
                    Cost_Type,
                    Record_Type,
                    {0}
                )
            '''.format(cols2) + myquery)

    def findcsrs(self):
        self.df['Fuzzy_File'] = self.df['Source_TBL'] + '_' + self.df['Source_ID']
        self.df['CSR_File_Name'] = None

        for index, row in self.df.iterrows():
            csr = [None, None]
            files = list(pl.Path(settings['LV_CSR_Dir']).glob('{}*.*'.format(row['Fuzzy_File'])))

            for file in files:
                modified = time.ctime(os.path.getmtime(file))

                if csr[0] is None or csr[0] < modified:
                    csr[0] = modified
                    csr[1] = file

            if csr[1]:
                row['CSR_File_Name'] = os.path.basename(csr[1])

        del self.df['Fuzzy_File']

    def map(self, gs_srvtype, col, source_tbl):
        df_results = pd.DataFrame()
        data = self.df.loc[(self.df['Gs_SrvType'] == gs_srvtype) & (self.df['Source_TBL'] == source_tbl)]

        if not data.empty:
            asql = SQLConnect('alch')
            asql.connect()

            if 'action_comment' not in data.columns:
                data['Action_Comment'] = None

            data['Error_Columns'] = None
            data['Error_Message'] = None

            asql.upload(data, 'mytbl')

            if asql.query("select object_id('mytbl2')").iloc[0, 0]:
                asql.execute("drop table mytbl2")

            asql.upload(asql.query('''
                with
                    MYTMP
                As
                (
                    select
                        {0},
                        iif(charindex(',',Gs_SrvID)>0,1,0) As BMB,
                        cast
                        (
                            '<head><page><![CDATA['
                                +
                            replace(Gs_SrvID, ',', ']]></page><page><![CDATA[')
                                +
                            ']]></page></head>' as XML
                        ) As tempVar

                    from mytbl
                ),
                    MYTMP2
                As
                (
                    select
                        {0},
                        BMB,
                        MY_Tbl.My_Col.value('.','VARCHAR(max)') Gs_SrvID

                    from MYTMP tempVar
                        cross apply tempvar.nodes('/head/page') MY_Tbl(My_Col)
                )

                select
                    *
                from MYTMP2'''.format(",".join(data.loc[:, data.columns != 'Gs_SrvID'].columns.values))), 'mytbl2')

            asql.execute("drop table mytbl;")

            asql.execute('''
                update A
                    set
                        A.Error_Columns = 'Gs_SrvType, Gs_SrvID',
                        A.Error_Message = Gs_SrvType + ' ' + cast(Gs_SrvID as varchar) + ' is not found in {0}'

                from mytbl2 As A
                left join {0} As B
                on
                    A.Gs_SrvID = B.{1}

                where
                    B.{1} is null'''.format(settings[gs_srvtype], col))

            asql.execute('''
                update A
                    A.Error_Columns = 'Gs_SrvType, Gs_SrvID',
                    A.Error_Message = 'BMB table is already mapped to ' + Gs_SrvType + ' ' + cast(Gs_SrvID as varchar)

                from mytbl2 As A
                inner join {0} As B
                on
                    A.Source_ID = B.BMI_ID
                        and
                    A.Gs_SrvType = B.Gs_SrvType
                        and
                    A.Gs_SrvID = B.Gs_SrvID

                where
                    A.BMB = 1'''.format(settings['BMB']))

            asql.execute('''
                update A
                    A.Error_Columns = 'Gs_SrvType, Gs_SrvID',
                    A.Error_Message = 'BMB table is already mapped to ' + Gs_SrvType + ' ' + cast(Gs_SrvID as varchar)

                from mytbl2 As A
                inner join {0} As B
                on
                    A.Source_ID = B.PCI_ID
                        and
                    A.Gs_SrvType = B.Gs_SrvType
                        and
                    A.Gs_SrvID = B.Gs_SrvID

                where
                    A.BMB = 1'''.format(settings['PCI_BMB']))

            if source_tbl == 'BMI':
                self.update_map(asql, source_tbl, settings['BMB'], settings['BMM'], settings['Unmapped'])
            else:
                self.update_map(asql, source_tbl, settings['PCI_BMB'], settings['PCI'], settings['PCI_Unmapped'])

            asql.execute("drop table mytbl2")

            asql.close()

            append_errors(df_results)

        del data, df_results

    def dispute(self, source, source_col):
        df_results = pd.DataFrame()
        data = self.df.loc[self.df['Source_TBL'] == source]

        if not data.empty:
            print("Processing disputes for {}".format(source))

            if 'Start_Date' not in data.columns:
                data['Start_Date'] = None
                data['Start_Date'] = data['Start_Date'].astype('datetime64[D]')

            if 'USI' not in data.columns:
                data['USI'] = None

            if 'PON' not in data.columns:
                data['PON'] = None

            if 'Action_Comment' not in data.columns:
                data['Action_Comment'] = None

            asql = SQLConnect('alch')
            asql.connect()

            asql.upload(data, 'mytbl')

            asql.execute('''
                update A
                    set A.Claim_Channel = 'Email'

                from mytbl As A

                where
                    A.Source_TBL = 'PCI'
                    ''')

            asql.execute('''
                update A
                    set A.Start_Date = dateadd(day, (-1 * C.Dispute_Limit) + 15, getdate())
                from mytbl As A
                inner join {1} As B
                on
                    A.Source_ID = B.{0}
                inner join {2} As C
                on
                    B.BAN = C.BAN

                where
                    A.Start_Date is null
                '''.format(source_col, settings[source], settings['Limitations']))

            asql.execute('''
                update A
                    set A.Start_Date = eomonth(dateadd(month, -1, A.Start_Date))
                from mytbl As A

                where
                    A.Start_Date is not null''')

            if asql.query("select object_id('mytbl2')").iloc[0, 0]:
                asql.execute("drop table mytbl2")

            if source == 'BMI':
                print("Grabbing MRC & OCC Cost")

                self.dispute_seeds(
                    asql,
                    data,
                    settings['MRC'],
                    'BDT_MRC_ID',
                    'B.Invoice_Date > A.Start_Date and A.Source_ID = B.BMI_ID',
                    'Amount > 0',
                    'MRC',
                    "'MRC'"
                )

                self.dispute_seeds(
                    asql,
                    data,
                    settings['OCC'],
                    'BDT_OCC_ID',
                    '''B.Invoice_Date > A.Start_Date and B.Vendor = C.Vendor
                        and B.BAN = C.BAN and B.BTN = C.WTN and B.Circuit_ID = C.Circuit_ID''',
                    'Amount > 0',
                    'OCC',
                    "upper(Activity_Type)",
                    "inner join {0} As C on A.Source_ID = C.BMI_ID".format(settings['BMI'])
                )
            else:
                print("Grabbing PaperCost MRC, NRC, and FRAC Cost")

                self.dispute_seeds(
                    asql,
                    data,
                    settings['PaperCost'],
                    'Seed',
                    'B.Bill_Date > A.Start_Date and A.Source_ID = B.PCI_ID',
                    'MRC > 0',
                    'PC-MRC',
                    "'MRC'"
                )

                self.dispute_seeds(
                    asql,
                    data,
                    settings['PaperCost'],
                    'Seed',
                    'B.Bill_Date > A.Start_Date and A.Source_ID = B.PCI_ID',
                    'NRC > 0',
                    'PC-NRC',
                    "'NRC'"
                )

                self.dispute_seeds(
                    asql,
                    data,
                    settings['PaperCost'],
                    'Seed',
                    'B.Bill_Date > A.Start_Date and A.Source_ID = B.PCI_ID',
                    'FRAC > 0',
                    'PC-FRAC',
                    "'FRAC'"
                )

            if asql.query("select object_id('mytbl2')").iloc[0, 0]:
                if asql.query("select object_id('DS')").iloc[0, 0]:
                    asql.execute("DROP TABLE DS")

                if asql.query("select object_id('DSB')").iloc[0, 0]:
                    asql.execute("DROP TABLE DSB")

                if asql.query("select object_id('DH')").iloc[0, 0]:
                    asql.execute("DROP TABLE DH")

                asql.execute("CREATE TABLE DSB (DSB_ID int, Stc_Claim_Number varchar(255))")
                asql.execute("CREATE TABLE DS (DS_ID int, DSB_ID int)")
                asql.execute("CREATE TABLE DH (DH_ID int, DSB_ID int)")

                print("Disputing {} cost".format(source))

                asql.execute('''
                    insert into {0}
                    (
                        USI,
                        STC_Claim_Number,
                        Source_TBL,
                        Source_ID
                    )

                    OUTPUT
                        INSERTED.DSB_ID,
                        INSERTED.Stc_Claim_Number

                    INTO DSB

                    select
                        USI,
                        '{1}_' + left(Record_Type,1) + cast(Seed as varchar),
                        Source_TBL,
                        Source_ID

                    from mytbl2;'''.format(settings['Dispute_Staging_Bridge'], self.getbatch()))

                asql.execute('''
                    insert into {0}
                    (
                        DSB_ID,
                        Rep,
                        STC_Claim_Number,
                        Dispute_Type,
                        Dispute_Category,
                        Audit_Type,
                        Cost_Type,
                        Cost_Type_Seed,
                        Record_Type,
                        Dispute_Reason,
                        PON,
                        Comment,
                        Confidence,
                        Batch_DT
                    )

                    OUTPUT
                        INSERTED.DS_ID,
                        INSERTED.DSB_ID

                    INTO DS

                    select
                        DSB.DSB_ID,
                        B.Full_Name,
                        '{2}_' + left(A.Record_Type,1) + cast(A.Seed as varchar),
                        A.Claim_Channel,
                        'GRT CNR',
                        'CNR Audit',
                        A.Cost_Type,
                        A.Seed,
                        A.Record_Type,
                        A.Action_Reason,
                        A.PON,
                        A.Action_Comment,
                        A.Confidence,
                        '{3}'

                    from mytbl2 As A
                    inner join {1} As B
                    on
                        A.Comp_Serial = B.Comp_Serial
                    inner join DSB
                    on
                        DSB.Stc_Claim_Number='{2}_' + left(A.Record_Type,1) + cast(A.Seed as varchar)
                '''.format(settings['DisputeStaging'], settings['CAT_Emp'], self.getbatch(), self.getbatch(True)))

                asql.execute('''
                    insert into {0}
                    (
                        DSB_ID,
                        Dispute_Category,
                        Display_Status,
                        Date_Submitted,
                        Dispute_Reason,
                        GRT_Update_Rep,
                        Date_Updated,
                        Source_File
                    )

                    OUTPUT
                        INSERTED.DH_ID,
                        INSERTED.DSB_ID

                    INTO DH

                    select
                        DSB.DSB_ID,
                        'GRT CNR',
                        'Filed',
                        getdate(),
                        A.Action_Reason,
                        B.Full_Name,
                        getdate(),
                        'GRT Email: ' + format(getdate(),'yyyyMMdd')

                    from mytbl2 As A
                    inner join {1} As B
                    on
                        A.Comp_Serial = B.Comp_Serial
                    inner join DSB
                    on
                        DSB.Stc_Claim_Number='{2}_' + left(A.Record_Type,1) + cast(A.Seed as varchar)

                    where
                        A.Claim_Channel = 'Email'
                '''.format(settings['Dispute_History'], settings['CAT_Emp'], self.getbatch()))

                asql.execute('''
                    insert into {0}
                    (
                        DS_ID,
                        DSB_ID,
                        DH_ID,
                        Open_Dispute
                    )
                    select
                        DS.DS_ID,
                        DS.DSB_ID,
                        DH.DH_ID,
                        1

                    from DSB
                    inner join DS
                    on
                        DSB.DSB_ID = DS.DSB_ID
                    left join DH
                    on
                        DSB.DSB_ID = DH.DSB_ID
                '''.format(settings['Dispute_Fact']))

                self.updatezerorev(asql, 'BMI', 'Dispute Review', 'Action_Comment', True)
                self.updatezerorev(asql, 'PCI', 'Dispute Review', 'Action_Comment', True)

                df_results = asql.query('''
                    with
                        MY_TMP
                    As
                    (
                        select distinct
                            Source_TBL,
                            Source_ID

                        from mytbl2
                    )

                    select
                        A.*

                    from mytbl As A
                    left join MY_TMP As B
                    on
                        A.Source_TBL = B.Source_TBL
                            and
                        A.Source_ID = B.Source_ID

                    where
                        B.Source_TBL is null''')

                if not df_results.empty:
                    df_results['Error_Columns'] = 'Source_TBL, Source_ID, Start_Date'
                    df_results['Error_Message'] = \
                        'Valid cost was not found for the Source_TBL, Source_ID, Start_Date combo'

                asql.execute("drop table mytbl, mytbl2, DS, DSB, DH")
            else:
                print("Warning! No cost found for {} of spreadsheet".format(source))

                df_results = asql.query('select * from mytbl')

                df_results['Error_Columns'] = 'Source_TBL, Source_ID, Start_Date'
                df_results['Error_Message'] = \
                    'Valid cost was not found for the Source_TBL, Source_ID, Start_Date combo'

                asql.execute("drop table mytbl")

            asql.close()

            append_errors(df_results)

        del data, df_results

    def sendtoprov(self):
        asql = SQLConnect('alch')
        asql.connect()

        self.df['Error_Columns'] = None
        self.df['Error_Message'] = None

        asql.upload(self.df, 'mytbl')

        asql.execute('''
            update A
                set
                    A.Error_Columns = 'Macnum',
                    A.Error_Message = 'This Macnum does not exist in CS'
            from mytbl As A
            left join {0} As B
            on
                A.Macnum = B.Macnum

            where
                B.MACNUM is null'''.format(settings['Cust_File']))

        asql.execute('''
            update A
                set
                    A.Error_Columns = 'Source_TBL, Source_ID',
                    A.Error_Message = 'This Source_TBL and Source_ID is already pending in Send to Prov table'

            from mytbl As A
            inner join {0} As B
            on
                A.Source_TBL = B.Source_TBL
                    and
                A.Source_ID = B.Source_ID

            where
                B.Root_Cause is null
                    and
                B.Prov_Note is null
                    and
                B.New_Root_Cause is null'''.format(settings['Send_To_Prov']))

        asql.upload(asql.query('''
            select distinct
                DATA.*,
                CNR.Vendor,
                CNR.BAN,
                CNR.BTN,
                CNR.WTN,
                CNR.Circuit_ID,
                CNR.MRC_Amount,
                CNR.Max_CNR_Date,
                MRC.Product_Type

            from mytbl As DATA
            inner join {0} As CNR
            on
                DATA.Source_TBL = CNR.Source_TBL
                    and
                DATA.Source_ID = CNR.Source_ID
            left join {1} As MRC
            on
                DATA.Source_TBL = 'BMI'
                    and
                CNR.Max_CNR_Date = MRC.Invoice_Date
                    and
                DATA.Source_ID = MRC.BMI_ID
        '''.format(settings['CNR'], settings['MRC'])), 'mytbl2')

        asql.execute('''
            insert into {0}
            (
                Batch,
                Audit_Name,
                Audit_Group,
                CAT_Rep,
                Source_TBL,
                Source_ID,
                Product,
                Vendor,
                BAN,
                BTN,
                WTN,
                Circuit_ID,
                Monthly_Impact,
                Invoice_Date,
                Macnum,
                Associated_PON,
                Issue,
                Category,
                Recommended_Action,
                Normalized_Reason,
                Sub_Reason
            )
            select
                eomonth(getdate()),
                'CNR',
                A.Audit_Group,
                B.Initials,
                A.Source_TBL,
                A.Source_ID,
                A.Product_Type,
                A.Vendor,
                A.BAN,
                A.BTN,
                A.WTN,
                A.Circuit_ID,
                A.MRC_Amount,
                A.Max_CNR_Date,
                A.Macnum,
                A.PON,
                A.Action_Reason,
                A.Prov_Category,
                A.Prov_Recommendation,
                A.Prov_Norm_Reason,
                A.Prov_Sub_Reason

            from mytbl2 As A
            inner join {1} As B
            on
                A.Comp_Serial = B.Comp_Serial
            where
                A.Error_Columns is null'''.format(settings['Send_To_Prov'], settings['CAT_Emp']))

        self.updatezerorev(asql,'BMI','Pending Prov', 'Action_Reason')
        self.updatezerorev(asql,'PCI','Pending Prov', 'Action_Reason')

        df_results = asql.query('''
            select
                *
            from mytbl

            where
                Error_Columns is not null''')

        asql.execute("drop table mytbl, mytbl2")

        asql.close()

        append_errors(df_results)

        del df_results

    def sendtolv(self):
        asql = SQLConnect('alch')
        asql.connect()

        self.df['Error_Columns'] = None
        self.df['Error_Message'] = None

        self.findcsrs()
        asql.upload(self.df, 'mytbl')

        asql.execute('''
            update A
                set
                    A.Error_Columns = 'Macnum',
                    A.Error_Message = 'This Macnum does not exist in CS'

            from mytbl As A
            left join {0} As B
            on
                A.Macnum = B.Macnum

            where
                B.Macnum is null
        '''.format(settings['Cust_File']))

        asql.execute('''
            update A
                set
                    A.Error_Columns = 'Source_TBL, Source_ID',
                    A.Error_Message = 'This Source_TBL and Source_ID is already pending in Send to LV table'

            from mytbl As A
            inner join {0} As B
            on
                A.Source_TBL = B.Source_TBL
                    and
                A.Source_ID = B.Source_ID

            where
                B.Is_Rejected is null
                    or
                B.Sugg_Action is null
        '''.format(settings['Send_To_LV']))

        asql.execute('''
            insert into {0}
            (
                Source_TBL,
                Source_ID,
                Vendor,
                BAN,
                BTN,
                WTN,
                Macnum,
                Rep,
                Invoice_Date,
                Comment,
                Batch,
                CSR_File_Name
            )
            select
                A.Source_TBL,
                A.Source_ID,
                C.Vendor,
                C.BAN,
                C.BTN,
                C.WTN,
                A.Macnum,
                B.Initials,
                C.Max_CNR_Date,
                A.Action_Reason,
                '{3}' As Batch,
                A.CSR_File_Name

            from mytbl As A
            inner join {1} As B
            on
                A.Comp_Serial = B.Comp_Serial
            left join {2} As C
            on
                A.Source_TBL = C.Source_TBL
                    and
                A.Source_ID = C.Source_ID

            where
                A.Error_Columns is null
        '''.format(settings['Send_To_LV'], settings['CAT_Emp'], settings['CNR'], self.getbatch(True, 7, 0)))

        self.updatezerorev(asql, 'BMI', 'Pending LV', 'Action_Reason')
        self.updatezerorev(asql, 'PCI', 'Pending LV', 'Action_Reason')

        df_results = asql.query('''
            select
                *
            from mytbl

            where
                Error_Columns is not null''')

        asql.execute("drop table mytbl")

        asql.close()

        append_errors(df_results)

        del df_results

    def adddn(self):
        asql = SQLConnect('alch')
        asql.connect()

        self.df['Error_Columns'] = None
        self.df['Error_Message'] = None

        asql.upload(self.df, 'mytbl')

        asql.execute('''
            update A
                set
                    A.Error_Columns = 'Source_TBL, Source_ID',
                    A.Error_Message = 'Dispute Notes were already filed for this Source_ID today'

            from mytbl As A
            inner join {0} As B
            on
                A.Source_TBL = B.Source_TBL
                    and
                A.Source_ID = B.Source_ID
            inner join {1} As C
            on
                B.DSB_ID = C.DSB_ID
                    and
                C.Open_Dispute = 1
            inner join {2} As D
            on
                C.DS_ID = D.DS_ID
                    and
                D.Dispute_Category = 'GRT CNR'
            inner join {3} As E
            on
                C.DH_ID = E.DH_ID
            inner join {4} As F
            on
                E.DH_ID = F.DH_ID
                    and
                cast(F.Edit_Date as date) = cast(getdate() as date)
            '''.format(settings['Dispute_Staging_Bridge'], settings['Dispute_Fact'], settings['DisputeStaging'],
                       settings['Dispute_History'], settings['Dispute_Notes']))

        asql.execute('''
            update A
                set
                    A.Error_Columns = 1

            from mytbl As A
            inner join {0} As B
            on
                A.Source_TBL = B.Source_TBL
                    and
                A.Source_ID = B.Source_ID
            inner join {1} As C
            on
                B.DSB_ID = C.DSB_ID
                    and
                C.Open_Dispute = 1
            inner join {2} As D
            on
                C.DS_ID = D.DS_ID
                    and
                D.Dispute_Category = 'GRT CNR'
            inner join {3} As E
            on
                C.DH_ID = E.DH_ID

            where
                A.Error_Columns is null
        '''.format(settings['Dispute_Staging_Bridge'], settings['Dispute_Fact'], settings['DisputeStaging'],
                   settings['Dispute_History']))

        asql.execute('''
            insert into {0}
            (
                DH_ID,
                Logged_By,
                Norm_Note_Action,
                Dispute_Note,
                Days_Till_Action,
                Edit_Date
            )
            select
                F.DH_ID,
                B.Full_Name,
                A.Action_Norm_Reason,
                A.Action_Reason,
                A.Amount_Or_Days,
                getdate()

            from mytbl A
            inner join {1} As B
            on
                A.Comp_Serial = B.Comp_Serial
            inner join {2} As C
            on
                A.Source_TBL = C.Source_TBL
                    and
                A.Source_ID = C.Source_ID
            inner join {3} As D
            on
                C.DSB_ID = D.DSB_ID
                    and
                D.Open_Dispute = 1
            inner join {4} As E
            on
                D.DS_ID = E.DS_ID
                    and
                E.Dispute_Category = 'GRT CNR'
            inner join {5} As F
            on
                D.DH_ID = F.DH_ID

            where
                A.Error_Columns='1'
        '''.format(settings['Dispute_Notes'], settings['CAT_Emp'], settings['Dispute_Staging_Bridge'],
                   settings['Dispute_Fact'], settings['DisputeStaging'], settings['Dispute_History']))

        self.updatezerorev(asql, 'BMI', 'Dispute Review', 'Action_Reason')
        self.updatezerorev(asql, 'PCI', 'Dispute Review', 'Action_Reason')

        df_results = asql.query('''
            select
                *
            from mytbl

            where
                isnull(Error_Columns,'') != '1'
        ''')

        if not df_results.empty and not df_results[df_results['Error_Columns'].isnull()].empty:
            df_results['Error_Message'].loc[df_results['Error_Columns'].isnull()] = \
                    'There are no open STC filed GRT CNR disputes for this Source_TBL & Source_ID combo'
            df_results['Error_Columns'].loc[df_results['Error_Columns'].isnull()] = 'Source_TBL, Source_ID'

        asql.execute("drop table mytbl")

        asql.close()

        append_errors(df_results)

        del df_results

    def addescalate(self):
        asql = SQLConnect('alch')
        asql.connect()

        asql.upload(self.df, 'mytbl')

        df_results = asql.query('''
            select
                A.*

            from mytbl As A
            left join {0} As B
            on
                B.Open_Dispute = 1
                    and
                B.Norm_Dispute_Category = 'GRT CNR'
            left join {1} As C
            on
                B.DSB_ID = C.DSB_ID
                    and
                A.Source_TBL = C.Source_TBL
                    and
                A.Source_ID = C.Source_ID
            left join {2} As D
            on
                B.DH_ID = D.DH_ID
                    and
                D.Display_Status = 'Denied - Pending'

            where
                D.DH_ID is null
        '''.format(settings['Dispute_Fact'], settings['Dispute_Staging_Bridge'], settings['Dispute_History']))

        df_results['Error_Columns'] = 'Source_TBL, Source_ID'
        df_results['Error_Message'] = \
            'There are no open Denied - Pending GRT CNR disputes for this Source_TBL & Source_ID combo'

        asql.execute("drop table mytbl")

        asql.close()

        append_errors(df_results)

        del df_results

    def addpaid(self):
        asql = SQLConnect('alch')
        asql.connect()

        asql.upload(self.df, 'mytbl')

        df_results = asql.query('''
            select
                A.*,
                case
                    when D.DH_ID is null then 'Source_TBL, Source_ID'
                    when isnumeric(A.amount) != 1 then 'Amount'
                    when A.amount <= 0 then 'Amount'
                    when isdate(A.Credit_Invoice_Date) != 1 then 'Credit_Invoice_Date'
                    when A.Credit_Invoice_Date != eomonth(A.Credit_Invoice_Date) then 'Credit_Invoice_Date'
                    when A.Credit_Invoice_Date < getdate() then 'Credit_Invoice_Date'
                end As Error_Columns,
                case
                    when D.DH_ID is null then 'There are no open Denied - Pending or Approved GRT CNR disputes for this Source_ID'
                    when isnumeric(A.amount) != 1 then 'Amount is not numeric'
                    when A.amount <= 0 then 'Amount is <= 0'
                    when isdate(A.Credit_Invoice_Date) != 1 then 'Credit_Invoice_Date is not a date'
                    when A.Credit_Invoice_Date != eomonth(A.Credit_Invoice_Date) then 'Credit_Invoice_Date is not the end of the month'
                    when A.Credit_Invoice_Date < getdate() then 'Credit_Invoice_Date is in the past'
                end As Error_Message

            from mytbl As A
            left join {0} As B
            on
                B.Open_Dispute = 1
                    and
                B.Norm_Dispute_Category = 'GRT CNR'
            left join {1} As C
            on
                B.DSB_ID = C.DSB_ID
                    and
                A.Source_TBL = C.Source_TBL
                    and
                A.Source_ID = C.Source_ID
            left join {2} As D
            on
                B.DH_ID = D.DH_ID
                    and
                D.Display_Status in ('Denied - Pending', 'Approved', 'Partial Approved')

            where
                D.DH_ID is null
                    or
                isnumeric(A.amount) != 1
                    or
                A.amount <= 0
                    or
                isdate(A.Credit_Invoice_Date) != 1
                    or
                A.Credit_Invoice_Date != eomonth(A.Credit_Invoice_Date)
                    or
                A.Credit_Invoice_Date < getdate()
        '''.format(settings['Dispute_Fact'], settings['Dispute_Staging_Bridge'], settings['Dispute_History']))

        asql.execute("drop table mytbl")

        asql.close()

        append_errors(df_results)

        del df_results

    def process(self):
        print("Processesing {} action".format(self.action))

        if self.action == 'Map':
            self.map('LL', 'ORD_WTN', 'BMI')
            self.map('LL', 'ORD_WTN', 'PCI')
            self.map('BRD', 'ORD_BRD_ID', 'BMI')
            self.map('BRD', 'ORD_BRD_ID', 'PCI')
            self.map('DED', 'CUS_DED_ID', 'BMI')
            self.map('DED', 'CUS_DED_ID', 'PCI')
            self.map('LD', 'ORD_WTN', 'BMI')
            self.map('LD', 'ORD_WTN', 'PCI')
            self.map('TF', 'ORD_POTS_ANI_BIL', 'BMI')
            self.map('TF', 'ORD_POTS_ANI_BIL', 'PCI')
        elif self.action == 'Dispute':
            self.dispute('BMI', 'BMI_ID')
            self.dispute('PCI', 'ID')
        elif self.action == 'Send to Prov':
            self.sendtoprov()
        elif self.action == 'Send to LV':
            self.sendtolv()
        elif self.action == 'Dispute Note' or self.action == 'Close Disputes':
            self.adddn()
        elif self.action == 'Escalate Disputes':
            self.addescalate()
        elif self.action == 'Paid Disputes':
            self.addpaid()
