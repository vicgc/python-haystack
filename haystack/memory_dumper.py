#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2011 Loic Jaquemet loic.jaquemet+python@gmail.com
#

__author__ = "Loic Jaquemet loic.jaquemet+python@gmail.com"

import logging
import argparse, ctypes, os, pickle, time, sys
import tarfile, zipfile
import tempfile, shutil


import dbg
import memory_mapping

log = logging.getLogger('dumper')


class MemoryDumper:
  ''' Dumps a process memory maps to a tgz '''
  def __init__(self,args):
    self.args = args
  
  def getMappings(self):
    return self.mappings

  def initPid(self):
    self.dbg = dbg.PtraceDebugger()
    self.process = self.dbg.addProcess(self.args.pid, is_attached=False)
    if self.process is None:
      log.error("Error initializing Process debugging for %d"% self.args.pid)
      raise IOError
      # ptrace exception is raised before that
    self.mappings = memory_mapping.readProcessMappings(self.process)
    log.debug('mappings read. Dropping ptrace on pid.')
    return
    
  def dumpMemfile(self):
    tmpdir = tempfile.mkdtemp()
    self.index = file(os.path.join(tmpdir,'mappings'),'w+')
    # test dump only the heap
    err=0
    for m in self.mappings:
      try:
        self.dump(m, tmpdir)
      except Exception,e:
        err+=1
        pass # no se how to read windows
    log.warning('%d mapping in error'%err)
    self.index.close()
    #continue() the process
    self.process.cont()
    self.dbg.deleteProcess(process=self.process)
    # 
    log.debug('Making a archive ')
    archive_name = os.path.normpath(self.args.dumpfile.name)
    self.archive(tmpdir, archive_name)
    #shutil.rmtree(tmpdir)
    log.debug('tmpdir is %s'%(tmpdir)) 
    log.debug('Dumped to %s'%(archive_name))
    return archive_name
      
  def dump(self, m, tmpdir):
    log.debug('Dumping %s to %s'%(m,tmpdir))
    # dump files to tempdir
    mname = "%s-%s" % (dbg.formatAddress(m.start), dbg.formatAddress(m.end))
    mmap_fname = os.path.join(tmpdir, mname)
    # we are dumping the memorymap content
    if ((self.args.heap or self.args.stack) and
      m.pathname not in ['[heap]','[stack]'] ): # restrict dump
      pass
    else:
      log.debug('Dumping the memorymap content')
      with open(mmap_fname,'wb') as mmap_fout:
        mmap_fout.write(m.mmap().getByteBuffer())
    log.debug('Dumping the memorymap metadata')
    self.index.write('%s,%s\n'%(mname, m.pathname))
    return 

  def archive(self, srcdir, name):
    tmpdir = tempfile.mkdtemp()
    tmpname = os.path.join(tmpdir, os.path.basename(name))
    log.debug('running shutil.make_archive')
    archive = shutil.make_archive(tmpname, 'gztar', srcdir)
    shutil.move(archive, name )
    shutil.rmtree(tmpdir ) # not working ?



class MemoryDumpLoader:
  ''' Loads a memory dump done by MemoryDumper.
  It's basically a tgz of all memorymaps 
  self.mapping should be a memory_mapping.Mappings isntance
  '''
  def __init__(self, dumpfile):
    self.dumpfile = dumpfile
    if not self.isValid():
      raise ValueError('memory dump not valid for %s '%(self.__class__))
    self.loadMappings()
  def getMappings(self):
    return self.mappings
  def isValid(self):
    raise NotImplementedError()
  def loadMappings(self):
    raise NotImplementedError()
    

class ProcessMemoryDumpLoader(MemoryDumpLoader):

  tarfn={ 'open': tarfile.open , 'openFile': 'extractfile' }
  zipfn={ 'open': zipfile.ZipFile , 'openFile': 'open' }
  indexFilename = 'mappings'
  filePrefix = './'
  
  def isValid(self):
    if self._test_tarfile() : 
      self.openArchive = self.tarfn['open']
      self.openFile_attrname = self.tarfn['openFile']
    elif self._test_zipfile() :
      self.openArchive = self.zipfn['open']
      self.openFile_attrname = self.zipfn['openFile']
    else:
      return False
    return True
    
  def _test_tarfile(self):
    try :
      self.archive = tarfile.open(None,'r', self.dumpfile)
      members = self.archive.getnames() # get the ./away
      if self.filePrefix+self.indexFilename not in members:
        log.error('no mappings index file in the tar archive.')
        return False
      #change prefix
      self.indexFilename=self.filePrefix+self.indexFilename
      self.mmaps = [ m for m in members if '-0x' in m ]
      if len(self.mmaps)>0:
        return True
    except tarfile.ReadError,e:
      log.info('Not a tar file')
    return False
    
  def _test_zipfile(self):
    try :
      self.archive = zipfile.ZipFile(self.dumpfile,'r' )
      members = self.archive.namelist() # get the ./away
      if self.indexFilename not in members:
        log.error('no mappings index file in the zip archive.')
        return False
      self.filePrefix=''
      self.mmaps = [ m for m in members if '-0x' in m ]
      if len(self.mmaps)>0:
        return True
    except zipfile.BadZipfile,e:
      log.info('Not a zip file')
    return False
      
        
  def loadMappings(self):
    mappingsFile = getattr(self.archive, self.openFile_attrname)(self.indexFilename)
    self.metalines = [l.strip().split(',') for l in mappingsFile.readlines()]
    self_mappings = []
    #for mmap_fname in self.mmaps:
    for mmap_fname, mmap_pathname in self.metalines:
      start,end = mmap_fname.split('-') # get the './' away
      start,end = int(start,16),int(end,16 )
      log.debug('Loading %s - %s'%(mmap_fname, mmap_pathname))
      mmap_content_file = getattr(self.archive, self.openFile_attrname)(self.filePrefix+mmap_fname)
      if isinstance(self.archive, zipfile.ZipFile): # ZipExtFile is lame
        log.warning('Using a local memory mapping . Zipfile sux. thx ruby.')
        mmap = memory_mapping.MemoryMapping( start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
        mmap = memory_mapping.LocalMemoryMapping.fromBytebuffer(mmap, mmap_content_file.read())
      elif end-start > 1000000: # use file mmap when file is too big
        log.warning('Using a file backed memory mapping. no mmap in memory for this memorymap (%s).'+
                    ' Search will fail. Buffer is needed.'%(mmap_pathname))
        mmap = memory_mapping.FileBackedMemoryMapping(mmap_content_file, start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
      else:
        log.debug('Using a MemoryDumpMemoryMapping. small size')
        mmap = memory_mapping.MemoryDumpMemoryMapping(mmap_content_file, start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
      self_mappings.append(mmap)
    self.mappings = memory_mapping.Mappings(self_mappings, os.path.normpath(self.dumpfile.name))
    return    


class LazyProcessMemoryDumpLoader(ProcessMemoryDumpLoader):
  def loadMappings(self):
    mappingsFile = getattr(self.archive, self.openFile_attrname)(self.indexFilename)
    self.metalines = [l.strip().split(',') for l in mappingsFile.readlines()]
    self_mappings = []
    #for mmap_fname in self.mmaps:
    for mmap_fname, mmap_pathname in self.metalines:
      metaOnly = False
      start,end = mmap_fname.split('-') # get the './' away
      start,end = int(start,16),int(end,16 )
      log.debug('Loading %s - %s'%(mmap_fname, mmap_pathname))
      try:
        mmap_content_file = getattr(self.archive, self.openFile_attrname)(self.filePrefix+mmap_fname)
      except KeyError, e:
        log.debug('Ignore absent file')
        mmap = memory_mapping.MemoryMapping( start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
        self_mappings.append(mmap)
        continue
      
      #else
      if isinstance(self.archive, zipfile.ZipFile): # ZipExtFile is lame
        log.warning('Using a local memory mapping . Zipfile sux. thx ruby.')
        mmap = memory_mapping.MemoryMapping( start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
        mmap = memory_mapping.LocalMemoryMapping.fromBytebuffer(mmap, mmap_content_file.read())
      elif end-start > 10000000: # use file mmap when file is too big
        log.warning('Using a file backed memory mapping. no mmap in memory for this memorymap. Search will fail. Buffer is needed.')
        mmap = memory_mapping.FileBackedMemoryMapping(mmap_content_file, start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
      else:
        log.debug('Using a MemoryDumpMemoryMapping. small size')
        mmap = memory_mapping.MemoryDumpMemoryMapping(mmap_content_file, start, end, permissions='rwx-', offset=0x0, 
                                major_device=0x0, minor_device=0x0, inode=0x0,pathname=mmap_pathname)
      self_mappings.append(mmap)
    self.mappings = memory_mapping.Mappings(self_mappings, os.path.normpath(self.dumpfile.name) )
    return    


class KCoreDumpLoader(MemoryDumpLoader):
  def isValid(self):
    # debug we need a system map to validate...... probably
    return True
    
  def getBaseOffset(self,systemmap):
    systemmap.seek(0)
    for l in systemmap.readlines():
      if 'T startup_32' in l:
        addr,d,n = l.split()
        log.info('found base_offset @ %s'%(addr))
        return int(addr,16)
    return None


  def getInitTask(self,systemmap):
    systemmap.seek(0)
    for l in systemmap.readlines():
      if 'D init_task' in l:
        addr,d,n = l.split()
        log.info('found init_task @ %s'%(addr))
        return int(addr,16)
    return None
    
  def getDTB(self,systemmap):
    systemmap.seek(0)
    for l in systemmap.readlines():
      if '__init_end' in l:
        addr,d,n = l.split()
        log.info('found __init_end @ %s'%(addr))
        return int(addr,16)
    return None
    
  def loadMappings(self):
    #DEBUG
    #start = 0xc0100000
    start = 0xc0000000
    end = 0xc090d000
    kmap = memory_mapping.MemoryDumpMemoryMapping(self.dumpfile, start, end, permissions='rwx-', offset=0x0, 
            major_device=0x0, minor_device=0x0, inode=0x0, pathname=os.path.normpath(self.dumpfile.name))
    self.mappings = memory_mapping.Mappings([kmap], os.path.normpath(self.dumpfile.name))




def dump(opt):
  dumper = MemoryDumper(opt)
  dumper.initPid()
  out = dumper.dumpMemfile()
  log.debug('process %d dumped to file %s'%(opt.pid, os.path.normpath(opt.dumpfile.name)))
  return os.path.normpath(opt.dumpfile.name)

def _load(opt):
  return load(opt.dumpfile,opt.lazy)

loaders = [ProcessMemoryDumpLoader,KCoreDumpLoader]

def load(dumpfile,lazy=True):
  loaderClass = ProcessMemoryDumpLoader
  if lazy:
    loaderClass = LazyProcessMemoryDumpLoader
  try:
    memdump = loaderClass(dumpfile)
    log.debug('%d dump file loaded'%(len(memdump.getMappings()) ))
  except IndexError,e: ### ValueError,e:
    log.warning(e)
    raise e
  return memdump.getMappings()

def argparser():
  rootparser = argparse.ArgumentParser(prog='memory_dumper', description='Dump process memory.')
  subparsers = rootparser.add_subparsers(help='sub-command help')

  dump_parser = subparsers.add_parser('dump', help="dump a pid's memory to file")
  dump_parser.add_argument('pid', type=int, action='store', help='Target PID')
  dump_parser.add_argument('dumpfile', type=argparse.FileType('wb'), action='store', help='The dump file')
  dump_parser.add_argument('--heap', action='store_const', const=True , help='Restrict dump to the heap')
  dump_parser.add_argument('--stack', action='store_const', const=True , help='Restrict dump to the stack')
  dump_parser.set_defaults(func=dump)  

  load_parser = subparsers.add_parser('load', help='search help')
  load_parser.add_argument('dumpfile', type=argparse.FileType('rb'), action='store', help='The dump file')
  load_parser.add_argument('--lazy', action='store_const', const=True , help='Lazy load')
  load_parser.set_defaults(func=_load)  
  return rootparser

def main(argv):
  logging.basicConfig(level=logging.INFO)
  parser = argparser()
  opts = parser.parse_args(argv)
  opts.func(opts)
  

if __name__ == '__main__':
  main(sys.argv[1:])
