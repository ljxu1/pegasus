import imp
import logging
import getpass
import os

from Pegasus.tools import properties
from Pegasus.tools import utils
from sqlalchemy import create_engine, orm, event, exc
from sqlalchemy.engine import Engine
from sqlite3 import Connection as SQLite3Connection
from urlparse import urlparse

from Pegasus import user as users

__all__ = ['connect']

log = logging.getLogger(__name__)

#-------------------------------------------------------------------
# Connection Properties
PROP_CATALOG_ALL_TIMEOUT = "pegasus.catalog.*.timeout"
PROP_CATALOG_ALL_DB_TIMEOUT = "pegasus.catalog.*.db.timeout"
PROP_CATALOG_MASTER_URL = "pegasus.catalog.master.url"
PROP_CATALOG_MASTER_TIMEOUT = "pegasus.catalog.master.timeout"
PROP_CATALOG_MASTER_DB_TIMEOUT = "pegasus.catalog.master.db.timeout"
PROP_CATALOG_REPLICA_DB_URL = "pegasus.catalog.replica.db.url"
PROP_CATALOG_REPLICA_TIMEOUT = "pegasus.catalog.replica.timeout"
PROP_CATALOG_REPLICA_DB_TIMEOUT = "pegasus.catalog.replica.db.timeout"
PROP_CATALOG_WORKFLOW_URL = "pegasus.catalog.workflow.url"
PROP_CATALOG_WORKFLOW_TIMEOUT = "pegasus.catalog.workflow.timeout"
PROP_CATALOG_WORKFLOW_DB_TIMEOUT = "pegasus.catalog.workflow.db.timeout"
PROP_DASHBOARD_OUTPUT = "pegasus.dashboard.output"
PROP_MONITORD_OUTPUT = "pegasus.monitord.output"

CONNECTION_PROPERTIES = [
    PROP_CATALOG_MASTER_URL,
    PROP_CATALOG_REPLICA_DB_URL,
    PROP_CATALOG_WORKFLOW_URL,
    PROP_DASHBOARD_OUTPUT,
    PROP_MONITORD_OUTPUT
]
#-------------------------------------------------------------------

class ConnectionError(Exception):
    pass


class DBType:
    JDBCRC = "JDBCRC"
    MASTER = "MASTER"
    WORKFLOW = "WORKFLOW"


class DBKey:
    TIMEOUT = "timeout"


def connect(dburi, echo=False, schema_check=True, create=False, pegasus_version=None, force=False, props=None,
            db_type=None, connect_args=None, verbose=True):
    """
    Connect to the provided URL database.
    :param dburi:
    :param echo:
    :param schema_check:
    :param create:
    :param pegasus_version:
    :param force:
    :param props:
    :param db_type:
    :param connect_args:
    :param verbose:
    :return:
    """
    dburi = _parse_jdbc_uri(dburi)
    _validate(dburi)

    try:
        log.debug("Connecting to: %s" % dburi)
        # parse connection properties
        connect_args = _parse_props(dburi, props, db_type, connect_args)

        engine = create_engine(dburi, echo=echo, pool_recycle=True, connect_args=connect_args)
        engine.connect()

    except exc.OperationalError, e:
        if "mysql" in dburi and "unknown database" in str(e).lower():
            raise ConnectionError("MySQL database should be previously created: %s (%s)" % (e.message, dburi))
        raise ConnectionError("%s (%s)" % (e.message, dburi))
    except Exception, e:
        raise ConnectionError("%s (%s)" % (e.message, dburi))

    Session = orm.sessionmaker(bind=engine, autoflush=False, autocommit=False,
                               expire_on_commit=False)
    db = orm.scoped_session(Session)

    # Database creation
    if create:
        try:
            from Pegasus.db.admin.admin_loader import db_create
            db_create(dburi, engine, db, pegasus_version=pegasus_version, force=force, verbose=verbose)

        except exc.OperationalError, e:
            raise ConnectionError("%s (%s)" % (e.message, dburi))

    if schema_check:
        from Pegasus.db.admin.admin_loader import db_verify
        db_verify(db, pegasus_version=pegasus_version, force=force)

    return db


def connect_by_submitdir(submit_dir, db_type, config_properties=None, echo=False, schema_check=True,
                         create=False, pegasus_version=None, force=False, cl_properties=None):
    """ Connect to the database from submit directory and database type """
    dburi = url_by_submitdir(submit_dir, db_type, config_properties, cl_properties=cl_properties)
    return connect(dburi, echo, schema_check, create=create, pegasus_version=pegasus_version, force=force)

    
def connect_by_properties(config_properties, db_type, cl_properties=None, echo=False, schema_check=True, create=False,
                          pegasus_version=None, force=False, verbose=True):
    """ Connect to the database from properties file and database type """
    props = properties.Properties()
    props.new(config_file=config_properties)
    _merge_properties(props, cl_properties)

    dburi = url_by_properties(config_properties, db_type, props=props)
    return connect(dburi, echo, schema_check, create=create, pegasus_version=pegasus_version, force=force,
                   db_type=db_type, props=props, verbose=verbose)


def url_by_submitdir(submit_dir, db_type, config_properties=None, top_dir=None, cl_properties=None):
    """ Get URL from the submit directory """
    if not submit_dir:
        raise ConnectionError("A submit directory should be provided with the type parameter.")
    if not db_type:
        raise ConnectionError("A type should be provided with the property file.")

    # From the submit dir, we need the wf_uuid
    # Getting values from the submit_dir braindump file
    top_level_wf_params = _parse_top_level_wf_params(submit_dir)

    # Load the top-level braindump now if top_dir is not None
    if top_dir is not None:
        # Getting values from the top_dir braindump file
        top_level_wf_params = _parse_top_level_wf_params(top_dir)

    # Get the location of the properties file from braindump
    top_level_prop_file = None
    
    # Get properties tag from braindump
    if "properties" in top_level_wf_params:
        top_level_prop_file = top_level_wf_params["properties"]
        # Create the full path by using the submit_dir key from braindump
        if "submit_dir" in top_level_wf_params:
            top_level_prop_file = os.path.join(top_level_wf_params["submit_dir"], top_level_prop_file)

    return url_by_properties(config_properties, db_type, submit_dir, top_dir=top_dir,
                             rundir_properties=top_level_prop_file, cl_properties=cl_properties)

    
def url_by_properties(config_properties, db_type, submit_dir=None, top_dir=None, rundir_properties=None,
                      cl_properties=None, props=None):
    """ Get URL from the property file """
    # Validate parameters
    if not db_type:
        raise ConnectionError("A type should be provided with the property file.")

    # Parse, and process properties
    if not props:
        props = properties.Properties()
        props.new(config_file=config_properties, rundir_propfile=rundir_properties)
        _merge_properties(props, cl_properties)

    if db_type.upper() == DBType.JDBCRC:
        dburi = _get_jdbcrc_uri(props)
    elif db_type.upper() == DBType.MASTER:
        dburi = _get_master_uri(props)
    elif db_type.upper() == DBType.WORKFLOW:
        dburi = _get_workflow_uri(props, submit_dir, top_dir)
    else:
        raise ConnectionError("Invalid database type '%s'." % db_type)

    if dburi:
        log.debug("Using database: %s" % dburi)
        return dburi

    raise ConnectionError("Unable to find a database URI to connect.")


def get_wf_uuid(submit_dir):
    # From the submit dir, we need the wf_uuid
    # Getting values from the submit_dir braindump file
    top_level_wf_params = utils.slurp_braindb(submit_dir)
    
    # Return if we cannot parse the braindump.txt file
    if not top_level_wf_params:
        log.error("Unable to process braindump.txt in %s" % (submit_dir))
        return None
    
    # Get wf_uuid for this workflow
    wf_uuid = None
    if (top_level_wf_params.has_key('wf_uuid')):
        wf_uuid = top_level_wf_params['wf_uuid']
    else:
        log.error("workflow id cannot be found in the braindump.txt ")
        return None
    
    return wf_uuid
    

def connect_to_master_db(user=None):
    "Connect to 'user's master database"

    if user is None:
        user = getpass.getuser()

    u = users.get_user_by_username(user)

    dburi = u.get_master_db_url()

    return connect(dburi)


#-------------------------------------------------------------------

# This turns on foreign keys for SQLite3 connections
@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(conn, record):
    if isinstance(conn, SQLite3Connection):
        log.trace("Turning on foreign keys")
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def _merge_properties(props, cl_properties):
    if cl_properties:
        for property in cl_properties:
            if "=" not in property:
                raise ConnectionError("Malformed property: %s" % property)
            key,value = property.split("=")
            props.property(key, val=value)


def _get_jdbcrc_uri(props=None):
    """ Get JDBCRC URI from properties """
    if props:
        replica_catalog = props.property('pegasus.catalog.replica')
        if not replica_catalog:
            raise ConnectionError("'pegasus.catalog.replica' property not set.")
        
        if replica_catalog.upper() != DBType.JDBCRC:
            return None

        rc_info = {
            "driver" : props.property('pegasus.catalog.replica.db.driver'),
            "url" : props.property(PROP_CATALOG_REPLICA_DB_URL),
            "user" : props.property('pegasus.catalog.replica.db.user'),
            "password" : props.property('pegasus.catalog.replica.db.password'),
        }

        url = rc_info["url"]
        if not url:
            raise ConnectionError("'%s' property not set." % PROP_CATALOG_REPLICA_DB_URL)
        url = _parse_jdbc_uri(url)
        o = urlparse(url)
        host = o.netloc
        database = o.path.replace("/", "")

        driver = rc_info["driver"]
        if not driver:
            raise ConnectionError("'pegasus.catalog.replica.db.driver' property not set.")
        
        if driver.lower() == "mysql":
            return "mysql://" + rc_info["user"] + ":" + rc_info["password"] + "@" + host + "/" + database

        if driver.lower() == "sqlite":
            if "sqlite:" in url:
                return url
            connString = os.path.join(host, "workflow.db")
            return "sqlite:///" + connString

        if driver.lower() == "postgresql":
            return "postgresql://" + rc_info["user"] + ":" + rc_info["password"] + "@" + host + "/" + database

        log.debug("Invalid JDBCRC driver: %s" % rc_info["driver"])
    return None
    
    
def _get_master_uri(props=None):
    """ Get MASTER URI """
    if props:
        dburi = props.property(PROP_CATALOG_MASTER_URL)
        if dburi:
            return dburi
        dburi = props.property(PROP_DASHBOARD_OUTPUT)
        if dburi:
            return dburi

    homedir = os.getenv("HOME", None)
    if homedir == None:
        raise ConnectionError("Environment variable HOME not defined, set %s property to point to the Dashboard database." % PROP_DASHBOARD_OUTPUT)
    
    dir = os.path.join( homedir, ".pegasus" );
    
    # check for writability and create directory if required
    if not os.path.isdir(dir):
        try:
            os.mkdir(dir)
        except OSError:
            raise ConnectionError("Unable to create directory: %s" % dir)
    elif not os.access(dir, os.W_OK):
        log.warning("Unable to write to directory: %s" % dir)
        return None
    
    #directory exists, touch the file and set permissions
    filename =  os.path.join(dir, "workflow.db")
    if not os.access(filename, os.F_OK):
        try:
            # touch the file
            open(filename, 'w').close()
            os.chmod(filename, 0600)
        except Exception, e:
            log.warning("unable to initialize MASTER db %s." % filename)
            log.exception(e)
            return None
    elif not os.access( filename, os.W_OK ):
        log.warning("No read access for file: %s" % filename)
        return None

    return "sqlite:///" + filename
    
    
def _get_workflow_uri(props=None, submit_dir=None, top_dir=None):
    """ Get WORKFLOW URI """
    if props:
        dburi = props.property(PROP_CATALOG_WORKFLOW_URL)
        if dburi:
            return dburi
        dburi = props.property(PROP_MONITORD_OUTPUT)
        if dburi:
            return dburi

    top_level_wf_params = None
    if submit_dir:
        # From the submit dir, we need the wf_uuid
        # Getting values from the submit_dir braindump file
        top_level_wf_params = _parse_top_level_wf_params(submit_dir)

    if top_dir:
        # Getting values from the top_dir braindump file
        top_level_wf_params = _parse_top_level_wf_params(top_dir)

    if not top_level_wf_params:
        return None

    # The default case is a .stampede.db file with the dag name as base
    dag_file_name = ""
    if top_level_wf_params.has_key('dag'):
        dag_file_name = top_level_wf_params['dag']
    else:
        raise ConnectionError("DAG file name cannot be found in the braindump.txt.")

    # Create the sqllite db url
    dag_file_name = os.path.basename(dag_file_name)
    output_db_file = os.path.join(top_level_wf_params["submit_dir"], dag_file_name[:dag_file_name.find(".dag")] + ".stampede.db")
    dburi = "sqlite:///" + output_db_file
    return dburi


def _parse_jdbc_uri(dburi):
    if dburi:
        if dburi.startswith("jdbc:"):
            dburi = dburi.replace("jdbc:", "")
            if dburi.startswith("sqlite:"):
                dburi = dburi.replace("sqlite:", "sqlite:///")
    return dburi


def _validate(dburi):
    try:
        if dburi:
            if dburi.startswith("postgresql:"):
                imp.find_module('psycopg2')
            if dburi.startswith("mysql:"):
                imp.find_module('MySQLdb')
            
    except ImportError, e:
        raise ConnectionError("Missing Python module: %s (%s)" % (e.message, dburi))


def _parse_props(dburi, props, db_type=None, connect_args=None):
    """

    :param db:
    :param props:
    :param db_type:
    :return:
    """
    if not connect_args:
        connect_args = {}

    if props and dburi.lower().startswith("sqlite") and db_type:
        if not DBKey.TIMEOUT in connect_args:
            try:
                timeout = None
                if db_type == DBType.MASTER:
                    timeout = _get_timeout_property(props, PROP_CATALOG_MASTER_TIMEOUT, PROP_CATALOG_MASTER_DB_TIMEOUT)
                elif db_type == DBType.WORKFLOW:
                    timeout = _get_timeout_property(props, PROP_CATALOG_WORKFLOW_TIMEOUT, PROP_CATALOG_WORKFLOW_DB_TIMEOUT)
                elif db_type == DBType.JDBCRC:
                    timeout = _get_timeout_property(props, PROP_CATALOG_REPLICA_TIMEOUT, PROP_CATALOG_REPLICA_DB_TIMEOUT)
                if not timeout:
                    timeout = _get_timeout_property(props, PROP_CATALOG_ALL_TIMEOUT, PROP_CATALOG_ALL_DB_TIMEOUT)

                if timeout:
                    connect_args[DBKey.TIMEOUT] = timeout

            except ValueError, e:
                raise ConnectionError("Timeout properties should be set in seconds: %s (%s)" % (e.message, dburi))

    return connect_args


def _get_timeout_property(props, prop_name1, prop_name2):
    """
    
    :param props:
    :param prop_name1:
    :param prop_name2:
    :return:
    """
    if props.property(prop_name1):
        return int(props.property(prop_name1))

    elif props.property(prop_name2):
        return int(props.property(prop_name2))

    return None


def _parse_top_level_wf_params(dir):
    """
    Parse the top level workflow parameters.
    :param dir: path of the directory
    :return: top level workflow parameters
    """
    top_level_wf_params = utils.slurp_braindb(dir)

    # Return if we cannot parse the braindump.txt file
    if not top_level_wf_params:
        raise ConnectionError("File 'braindump.txt' not found in %s" % (dir))

    return top_level_wf_params
