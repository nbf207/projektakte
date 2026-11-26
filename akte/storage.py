"""Durability helpers for SSD storage; directory fsync is supported on Linux."""
import os
from pathlib import Path


def sync_directory(directory):
    if os.name!='nt':
        fd=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try: os.fsync(fd)
        finally: os.close(fd)


def durable_replace(source,target):
    source,target=Path(source),Path(target)
    with source.open('r+b') as file: os.fsync(file.fileno())
    os.replace(source,target)
    sync_directory(target.parent)
    sync_directory(target.parent.parent)
