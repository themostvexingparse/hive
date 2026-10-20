"""WSGI entry point, e.g. for PythonAnywhere:

    import os
    os.environ["HIVE_ADMIN_TOKEN"] = "..."
    os.environ["HIVE_DATABASE_URL"] = "mysql+pymysql://user:pass@host/db"
    from hive.server.wsgi import application

Serves every project: the default one at /, others at /p/<project>/ (see projects.py).
"""

from .router import create_server

application = create_server()
