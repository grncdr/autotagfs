#!/usr/bin/env python
from __future__ import with_statement
from errno import EACCES
#from os.path import realpath
from sys import argv, exit
from time import time
import re
import os
from fuse import FUSE, Operations, LoggingMixIn

class AutoTag(LoggingMixIn, Operations):    
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.format = "%A/%a/%t"
        self.cache = PathCache(self.root)
        
    def __call__(self, op, path, *args):
        return LoggingMixIn.__call__(self, op, self.root + path, *args)
    
    def access(self, path, mode):
        if not os.access(path, mode):
            raise OSError(EACCES, '')
    
    chmod = os.chmod
    chown = os.chown
    create = None
    def flush(self, path, fh):
        return os.fsync(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)
                
    def getattr(self, path, fh=None):
        if os.path.islink(path):
            return self.getattr(self.readlink(path))
        st = os.lstat(path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))
    
    getxattr = None
    
    link = None
    #def link(self, target, source):
    #    return os.link(source, target)
    
    listxattr = None
    mkdir = os.mkdir

    mknod = os.mknod
    open = os.open
    readlink = os.readlink
        
    def read(self, path, size, offset, fh):
        file = self.cache.get(path)
        inTag = splitRead = 0
        bytes = ''
        if offset <= file.fakeTag.length:
            print "Reading tag, offset =", offset, ", size=", size
            if offset + size <= file.fakeTag.length:
                return file.fakeTag.string[offset:size]
            bytes = bytes + file.fakeTag.string[offset:]
            size = size - len(bytes)
            offset = file.origTagSize
        else:
            offset = offset + (file.origTagSize - file.fakeTag.length)
        print "Seaking to offset:",offset
        os.lseek(fh, offset, 0)
        return bytes + os.read(fh, size)

    def readdir(self, path, fh):
        return ['.', '..'] + os.listdir(path)

    def release(self, path, fh):
        return os.close(fh)
        
    def rename(self, old, new):
        return os.rename(old, self.root + new)
    
    rmdir = os.rmdir
    
    def statfs(self, path):
        stv = os.statvfs(path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
            'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
            'f_frsize', 'f_namemax'))
    
    def symlink(self, target, source):
        if os.path.isdir(source):
            self.mkdir(target)
            for entry in os.listdir(source):
                self.symlink(target+'/'+entry, source+'/'+entry)
        else:
    		return os.symlink(source, target)
    
    def truncate(self, path, length, fh=None):
        with open(path, 'r+') as f:
            f.truncate(length)
    
    unlink = os.unlink
    utimens = os.utime
    
    def write(self, path, data, offset, fh):
        os.lseek(fh, offset, 0)
        return os.write(fh, data)
    

class PathCache(dict):
    def __init__(self, root):
        self.root = root
        self.history = PathHistory(10)
        self.maxSize = 1000

    def get(self,path):
        self.history.append(path)
        if self.has_key(path):
            if self[path].updated < os.path.getmtime(path):
                self[path].update()
            return self[path]
        self.add(path)
        return self[path]

    def add(self, path):
        # FIXME - This method should parse the mime type of the path and choose the appropiate TaggableFile object
        self[path] = MP3File(path, self.root)
        if len(self) > self.maxSize:
            del self[self.history[0]]

class PathHistory(list):
    def __init__(self, maxSize):
        self.maxSize = maxSize

    def append(self,item):
        if len(self) == self.maxSize:
            self.pop(0)
        list.append(self, item)

class MP3File():
    def __init__(self, path, root):
        self.path = path
        self.fakeTag = ID3Tag(path, root)
        self.update()

    def update(self):
        self.parseRealTag()
        self.updated = time()

    def parseRealTag(self):
        file = open(self.path,"rb")
        #No tag at start of file, nothing to worry about here
        if file.read(3) != 'ID3':
            return 0
        version = file.read(2)
        flags = file.read(1)
        size = [(ord(b) & 0x7F) for b in file.read(4)]
        size = size[0] << 21 | size[1] << 14 | size[2] << 7 | size[3]
        file.close()
        self.origTagSize = size

class FakeTag():
    patterns = {
      'year': re.compile(r'(\d{4}(?: - | |-))'),
      'track': re.compile(r'(\d+(?: - | |-))'),
    }


    def __init__(self, path, root):
        self.path = path
        self.root = root
        # Split up path
        path = path.replace(root+'/','').split('/')
        artist, album, title = path[0], path[1], path[2]
        del(path)
        title = title[0:title.rfind('.')]
        # Check for year in album 
        if self.patterns['year'].match(album):
            year = self.patterns['year'].findall(album)[0]
            album = album[len(year):-1]
            year = year[0:3]
        # Check for track number in title 
        if self.patterns['track'].match(title):
            track = self.patterns['track'].findall(title)[0]
            title = title[len(track):]
            track = track.replace(' ','').replace('-','')
        self.data = locals()
        del(self.data['self'])
        del(self.data['root'])

class ID3Tag(FakeTag):
    map = {
            'artist': 'TPE1', 
            'album': 'TALB',
            'title': 'TIT2',
            'track': 'TRCK',
            'year': 'TYER'
    }
    version = chr(3) + chr(0)
    flags   = chr(0)

    def __init__(self, path, root):
        FakeTag.__init__(self,path,root)
        tag = ''
        # Create frames
        for k in self.data.keys():
            frame = ID3Tag.map[k]
            frame = frame + self.sizeToByteString( len(self.data[k]) )
            frame = frame + chr(0b00000000) + self.data[k]
            tag = tag + frame
        size = self.sizeToByteString(len(tag))
        self.string = 'ID3' + self.version + self.flags + size + tag
        self.length = len(self.string)

    def __len__(self):
        return self.length

    def __str__(self):
        return self.string

    def sizeToByteString(self, size):
        size = size & 0x0FFFFFFF
        str = ''
        for i in range(0,21,7):
            str = str + chr(size >> i & 0x7F)
        return str

if __name__ == "__main__":
    if len(argv) != 3:
        print 'usage: %s <source> <mountpoint>' % argv[0]
        exit(1)
    fuse = FUSE(AutoTag(argv[1]), argv[2], foreground=True)
