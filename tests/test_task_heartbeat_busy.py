import sqlite3,threading
import pytest
from app import db,tasks


def test_heartbeat_survives_catalog_adoption_lock(monkeypatch):
    done=threading.Event();calls=[]
    def execute(sql,args):
        calls.append(args)
        if len(calls)==1:raise sqlite3.OperationalError('database is locked')
        done.set()
    monkeypatch.setattr(db,'execute',execute)
    tasks.heartbeat_task('task',2,done,interval=0)
    assert len(calls)==2 and calls[-1][-2:]==('task',2)


def test_heartbeat_does_not_hide_other_database_errors(monkeypatch):
    def execute(*args):raise sqlite3.OperationalError('no such table: tasks')
    monkeypatch.setattr(db,'execute',execute)
    with pytest.raises(sqlite3.OperationalError,match='no such table'):
        tasks.heartbeat_task('task',2,threading.Event(),interval=0)
