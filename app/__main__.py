"""Run with python -m app. Binds loopback; never exposes keys over the network."""
import os
import uvicorn
from .main import create_app

if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=int(os.getenv('CITE_AUDIT_PORT', '8000')), log_level='info', access_log=False)
