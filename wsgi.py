"""WSGI entry point for production servers, e.g. `gunicorn wsgi:app`.
Importing app also creates the database tables and seeds defaults on first boot.
"""
from app import app

if __name__ == "__main__":
    app.run()
