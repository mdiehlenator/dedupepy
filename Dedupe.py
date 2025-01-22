#!/usr/bin/python3

import argparse
#! Is this needed?
import hashlib
import os
import pathlib
import re
import sqlite3
import time
import zlib
import fnmatch
from pprint import pprint, pformat

ver_major = 1
ver_minor = 1
previous_size = {}

class Config:

    def __init__(self):

        # name, mandatory, multi-value, default, match
        valid_args = [
            {"cwd",             1, 0, ".", ".+"},
            {"database",        1, 0, "default.sqlite", ".+"},
            {"batch",           0, 0, "no", "yes|no"},
            {"loglevel",        0, 1, "info", "none|info|debugging|verbose"},
            {"scan",            0, 0, "no", "yes|no"},
            {"resume",          0, 0, "no", "yes|no"},
            {"report",          0, 0, "no", "yes|no"},
            {"ignore_empty",    0, 0, "no", "yes|no"},
            {"skip_dir_exact",  0, 1, "", ".+"},
            {"skip_dir_glob",   0, 1, "", ".+"},
            {"skip_file_exact", 0, 1, "", ".+"},
            {"skip_file_glob",  0, 1, "", ".+"},
        ]

        self.empty_count = 0
        self.empty_count = 0
        self.processed_count = 0
        self.skipped_count = 0
        self.start_time = 0
        self.end_time = 0

        self.conf_file = self.process_args(valid_args)

        #! Factor this back into it's own function.
        comment = re.compile("^#.+")
        space = re.compile("^\s?$")

        try:
            f = open(self.conf_file)
        except:
            print(f"An error occured wile trying to open config file: {self.conf_file}")
            exit()

        for line in f.readlines():
            if comment.match(line) is not None:
                continue
            if space.match(line) is not None:
                continue

            line = line.rstrip()
            var, val = line.split(maxsplit=1)

            if self[var] == "None":
                self[var] =  val
                continue

            val = (self[var], val)
            # print(f"Adding multi-value: {var} {val}")
            self[var] = val

    def process_args(self, valid_args):
        argparser = argparse.ArgumentParser(exit_on_error=False)
        argparser.add_argument("conffile")
        args = argparser.parse_args()
        return args.conffile


    def __getitem__(self, key):
        return getattr(self, key, "None")

    def __setitem__(self, var, val):
        if val == None:
            val = "None"

        setattr(self, var, val)

class DB:
    def __init__(self,c):
        self.config = c
        self.db_name = c["database"]
        self.resume = c["resume"]

        if self.resume == "yes":
            log(c, "info", f"XXX Resuming from old db: {c['database']}")
            self.connect()
        else:
            log(c, "info", f"XXX Removing old db: {c['database']}")
            p = pathlib.Path(c["database"])
            p.unlink(missing_ok=True)
            self.connect()
            self.create()

    def version_check(self,M,m):
        return 1;

    def calculate_crc_by_size(self, size):
        print(f"Dupe by size: {size}")

        #! log(self.config, "info", "Potential dupe.")
        self.db_cursor.execute("select path from files where size=? and crc not in (0,-1)", [size])
        m = self.db_cursor.fetchall()

        for path in m:
            #! We need to compare the mtime in the db v fs to see 
            #! if we need to recalculate crc
            f = path[0]
            crc = self.crc(f)
            self.store_crc_by_path(f, crc)
            print(f"{path} = {crc}")

    def store_crc_by_path(self, f, crc):
        self.db_cursor.execute("update files set crc=? where path=?",
        [crc, f])
        self.db.commit()


    def crc(self,path):
        prev = 0
        for eachline in open(path, "rb"):
            prev = zlib.crc32(eachline, prev)

        return "%X" % (prev & 0xFFFFFFFF)

    def close(self):
        self.db.close()

    def create(self):
        self.db_cursor.execute("create table version (major int default 0, minor int default 0)")
        self.db_cursor.execute("insert into version (major, minor) values (1,0)")
        
        self.db_cursor.execute("""
        create table files (
            filename varchar(255) default '',
            dir varchar(255) default '',
            path varchar(512) default '',
            size int default -1,
            mtime float default 0.0,
            crc char(8) default '',
            dupe int default 0
            );
        """
        )

        self.db_cursor.execute("create index files_filename ON files(filename);")
        self.db_cursor.execute("create index files_dir ON files(dir);")
        self.db_cursor.execute("create index files_path ON files(path);")
        self.db_cursor.execute("create index files_crc ON files(crc);")

        self.db.commit()

    def save(self,f):
        # log(self.config, "debug", pformat(f))
        
        self.db_cursor.execute("""
        insert into files (filename, dir, path, size, mtime, crc, dupe) values 
        (?, ?, ?, ?, ?, ?, ?)
        """,
        [ f['file'], f['dir'], f['path'], f['size'], f['mtime'], f['crc'], 0]
        )
        self.db.commit()

    def connect(self):
        try:
            self.db = sqlite3.connect(self.db_name)
        except:
            print(f"Error opening database: {self.db_name}")
            exit()

        self.db_cursor = self.db.cursor()
        log(self.config, "info", "XXX Connected to db")


def main():
    c = Config()
    c["start_time"] = time.time()

    db = DB(c)

    #! This needs to be somewhere else.  DB?
    if (db.version_check(ver_major, ver_minor) == 0):
        print("Wrong version of db")
        exit

    if c['scan'] == 'yes':
        #! There may be a better method that would allow us to skip entire directories
        for root, dirs, files in os.walk(c["cwd"]):
            for f in files:
                # CWD shows up on walk w/o file
                if f == " ":
                    continue

                if ignore(c, root, f):
                    c["skipped_count"] = c["skipped_count"] + 1
                else:
                    o = process_file(root, f, c, db)

                    store(c, db, o)

                    if o['size'] in previous_size:
                        db.calculate_crc_by_size( o["size"] )

                    previous_size[ o["size"] ] = 1

    print("Done scanning")
    c["end_time"] = time.time()

    if c["report"] == "yes":
        do_report(c)

    do_actions()

    db.close()

#! More needed here.
def ignore(c, dir, file):

    p = pathlib.Path(file)
    if p.is_socket():
        print(f"Socket: {file}")
        return True

    if p.is_fifo():
        print(f"Fifo: {file}")
        return True

    if p.is_symlink():
        print(f"Simlink: {file}")
        return True

    if p.is_block_device():
        print(f"Block device: {file}")
        return True

    if p.is_char_device():
        print(f"Char device: {file}")
        return True

    if ignore_dir(c, dir):
        return True

    if ignore_file(c, file):
        return True

    return False

def ignore_dir(c, dir):

    skip_dirs = c["skip_dir_exact"]
    if isinstance(skip_dirs, str):
        if exact_match(skip_dirs, dir):
            print(f"Skipping dir: {dir}")
            return True
    else:
        for skip_dir in skip_dirs:
            if exact_match(skip_dir, dir):
                print(f"Skipping dir: {dir}")
                return True

    skip_dirs = c["skip_dir_glob"]
    if isinstance(skip_dirs, str):
        if glob_match(skip_dirs, dir):
            print(f"Skipping dir glob: {dir}")
            return True
    else:
        for skip_dir in skip_dirs:
            if glob_match(skip_dir, dir):
                print(f"Skipping dir glob: {file}")
                return True    
    return False

def ignore_file(c, file):
    skip_files = c["skip_file_exact"]
    if isinstance(skip_files, str):
        if exact_match(skip_files, file):
            print(f"Skipping file: {file}")
            return True
    else:
        for skip_file in skip_files:
            if exact_match(skip_file, file):
                print(f"Skipping file: {file}")
                return True

    skip_files = c["skip_file_glob"]
    if isinstance(skip_files, str):
        if glob_match(skip_files, file):
            print(f"Skipping file glob: {file}")
            return True
    else:
        for skip_file in skip_files:
            if glob_match(skip_file, file):
                print(f"Skipping file glob: {file}")
                return True
    
    return False

def exact_match(skip, s):
    if skip == s:
        return True
    return False

def glob_match(skip, s):
    if fnmatch.fnmatch(s, skip):
        return True

def process_file(root, filename, c, db):
    o = {}

    o["dir"] = root
    o["file"] = filename
    o["path"] = os.path.join(root, filename)
    o["size"] = os.path.getsize(o["path"])
    o["mtime"] = os.path.getmtime(o["path"])
    o["crc"] = -1

    return o

def store(c, db, f):
    if c["ignore_empty"] == "yes" and f["size"] == 0:
        c["empty_count"] = c["empty_count"]+1
        return

    c["processed_count"] = c["processed_count"]+1

    db.save(f)


#! Is this needed?
def hash(path):
    m = hashlib.md5()
    for line in open(path, "rb"):
        m.update((line))
    return m.hexdigest()


def do_report(c):
    #! Revisit indexes based on what queries we run here.
    print("======================================")
    print(f"Total Processed: {c['processed_count']}")
    print(f"Total Skipped: {c['skipped_count']}")
    print(f"Total Empty: {c['empty_count']}")

    rt = round(c["end_time"] - c["start_time"], 2)
    if rt == 0:
        rt = 1

    print(f"Total Run-time: {rt} seconds")
    print(f"Files/sec: {c['processed_count']/rt}")

    print(f"Test: {c['database']}")
    print("======================================")

def log(c, level, msg):
    print(msg)

#! Not implemented
def do_actions():
    #! Revisit indexes based on what queries we run here.
    pass


if __name__ == "__main__":
    main()
