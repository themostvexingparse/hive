"""WSGI entry point, e.g. for PythonAnywhere:

    import os
    os.environ["HIVE_ADMIN_TOKEN"] = "..."
    os.environ["HIVE_DATABASE_URL"] = "mysql+pymysql://user:pass@host/db"
    from hive.server.wsgi import application
"""

from .app import create_app

application = create_app()
