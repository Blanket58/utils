import itertools
from pathlib import Path

import sqlparse
import tomllib
from odps import ODPS, dbapi, options
from odps.errors import NoPermission
from pandas import DataFrame
from platformdirs import user_documents_dir
from sql_metadata import Parser

from .decorator import timer


class OdpsConnector:
    """Connect and get data from database."""

    def __init__(self):
        """Start a session and create a cursor."""
        options.sql.settings = {
            "odps.sql.allow.fullscan": "true",
            "odps.sql.validate.orderby.limit": "false",
            "odps.sql.udf.getjsonobj.new": "true"
        }
        config = tomllib.load(Path(user_documents_dir(), "odps-dependencies", "odps_config.toml").open(mode="rb"))
        self.conn = dbapi.connect(ODPS(**config))
        self.cursor = self.conn.cursor()

    @staticmethod
    def as_pandas(cursor, coerce_float=False):
        """Return a pandas `DataFrame` out of a dbapi compatible cursor.

        This will pull the entire result set into memory.  For richer pandas-like
        functionality on distributed data sets, see the Ibis project.

        Parameters
        ----------
        cursor : `:py:class:Cursor`
            The cursor object that has a result set waiting to be fetched.

        coerce_float : bool, optional
            Attempt to convert values of non-string, non-numeric objects to floating
            point.

        Returns
        -------
        DataFrame
        """
        names = [metadata[0] for metadata in cursor.description]
        return DataFrame.from_records(cursor.fetchall(), columns=names, coerce_float=coerce_float)

    @timer
    def execute(self, sql, **kwargs):
        """
        一次运行以`;`隔开的多段SQL并返回结果

        Parameters
        ----------
        sql: str

        Returns
        -------
        DataFrame
        """
        sql = sqlparse.format(sql, strip_comments=True)
        sql_list = sqlparse.split(sql)
        for x in sql_list:
            self.cursor.execute(x)
        return self.as_pandas(self.cursor)

    @timer
    def fetch(self, sql, tag, **kwargs):
        """
        根据Tag从多段带有指定注释格式的SQL中选取想要的段落执行并返回结果

        Parameters
        ----------
        sql: str
        tag: str
            Select exactly one query by comment like `/*COMMENT*/` locate at the start and get the result.

        Examples
        --------
        >>> s = "describe ods.table; /*I want this one*/select * from ods.table2;"
        >>> with OdpsConnector() as db:
        >>>     df = db.fetch(s, tag="I want this one")
        """
        sql_list = sqlparse.split(sql)
        sql_list = list(filter(lambda x: x.startswith(f"/*{tag}*/"), sql_list))
        assert len(sql_list) == 1, "请检查tag名，重复或不存在"
        sql = sqlparse.format(sql_list.pop(), strip_comments=True)
        self.cursor.execute(sql)
        return self.as_pandas(self.cursor)

    @timer
    def upload(self, data, name, schema, dtypes, if_exists="fail", **kwargs):
        """
        Upload data to impala database.

        Parameters
        ----------
        data: pandas DataFrame | List of tuples
            Data.
        name: str
            Name of SQL table.
        schema: str
            Specify the schema.
        dtypes: dict
            Specify each columns' data type.
        if_exists: {"fail", "replace", "append"}, default "fail"
            How to behave if the table already exists.

            * fail: Raise an AssertError.
            * replace: Drop the table before inserting new values.
            * append: Insert new values to the existing table.

        Examples
        --------
        >>> with OdpsConnector() as db:
        >>>     db.upload(data, "test_table", "test", dtypes={"dt": "string", "userid": "bigint", "amount": "float"})
        """
        assert if_exists in ("fail", "replace", "append"), "Invalid value for 'if_exists'."
        if if_exists == "replace":
            self.cursor.execute(f"drop table if exists {schema}.{name}")
            table = ", ".join([f"{i} {v}" for i, v in dtypes.items()])
            self.cursor.execute(f"create table {schema}.{name} ({table})")
        else:
            if if_exists == "append":
                assert self.conn.odps.exist_table(name, schema=schema), "Table doesn't exist."
            else:
                assert not self.conn.odps.exist_table(name, schema=schema), "Table already exist."
                table = ", ".join([f"{i} {v}" for i, v in dtypes.items()])
                self.cursor.execute(f"create table {schema}.{name} ({table})")
        columns = str(tuple(dtypes.keys())).replace("'", "`")
        if isinstance(data, list):
            values = data
        else:
            values = data.apply(lambda x: tuple(x), axis=1).values.tolist()
        values = str(values).strip("[]")
        self.cursor.execute(f"insert into {schema}.{name} {columns} values {values}")

    def close(self):
        self.conn.close()

    def __call__(self, *args, **kwargs):
        return self.execute(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def parse_tables_from_sql(sql):
    tables = Parser(sql).tables
    return list(filter(lambda x: '.' in x, tables))


def parse_tables_from_sqls(sqls):
    sql_list = sqlparse.split(sqls)
    tables = [parse_tables_from_sql(sql) for sql in sql_list]
    return list(itertools.chain.from_iterable(tables))


def check_tables_privileges(tables):
    with OdpsConnector() as db:
        result = {}
        for table in tables:
            schema, table_name = table.split('.')
            try:
                db.conn.odps.get_table(name=table_name, project=schema, schema=schema).table_schema
                result[table] = True
            except NoPermission:
                result[table] = False
    return result
