#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2011 Loic Jaquemet loic.jaquemet+python@gmail.com
#

'''
This module is the main aspect of haystack.
The whole point of this module is to search a memory space for a valid 
C structure.
'Valid' means:
  * a pointer should have a pointer value.
  * a string whould be a string.
  * a constrainged integer should be in a range of acceptable values.
  etc...

You can implement your own structures easily by registering your module 
containing ctypes Structure :

  # mymodule.py
  from haystack import model
  
  [... structure definition .. ]
  
  model.registerModule(sys.modules[__name__])

--------------------------------------------------------------------------------

Structure definition is done strictly within ctypes habits.
But as a bonus, you can add some constraints on the structure members by adding
a member expectedValues on the Python object.

Example :

  # ctypes_openssl.py
  from haystack import model

  class RSA(OpenSSLStruct):
    """ rsa/rsa.h """
    _fields_ = [
    ("pad",  ctypes.c_int), 
    ("version",  ctypes.c_long),
    ("meth",ctypes.POINTER(BIGNUM)),#const RSA_METHOD *meth;
    ("engine",ctypes.POINTER(ENGINE)),#ENGINE *engine;
    ('n', ctypes.POINTER(BIGNUM) ), ## still in ssh memap
    ('e', ctypes.POINTER(BIGNUM) ), ## still in ssh memap
    ('d', ctypes.POINTER(BIGNUM) ), ## still in ssh memap
    ('p', ctypes.POINTER(BIGNUM) ), ## still in ssh memap
    ('q', ctypes.POINTER(BIGNUM) ), ## still in ssh memap
    ('dmp1', ctypes.POINTER(BIGNUM) ),
    ('dmq1', ctypes.POINTER(BIGNUM) ),
    ('iqmp', ctypes.POINTER(BIGNUM) ),
    ("ex_data", CRYPTO_EX_DATA ),
    ("references", ctypes.c_int),
    ("flags", ctypes.c_int),
    ("_method_mod_n", ctypes.POINTER(BN_MONT_CTX) ),
    ("_method_mod_p", ctypes.POINTER(BN_MONT_CTX) ),
    ("_method_mod_q", ctypes.POINTER(BN_MONT_CTX) ),
    ("bignum_data",ctypes.POINTER(ctypes.c_ubyte)), ## moue c_char_p ou POINTER(c_char) ?
    ("blinding",ctypes.POINTER(BIGNUM)),#BN_BLINDING *blinding;
    ("mt_blinding",ctypes.POINTER(BIGNUM))#BN_BLINDING *mt_blinding;
    ]
    expectedValues={
      "pad": [0], 
      "version": [0], 
      "references": RangeValue(0,0xfff),
      "n": [NotNull],
      "e": [NotNull],
      "d": [NotNull],
      "p": [NotNull],
      "q": [NotNull],
      "dmp1": [NotNull],
      "dmq1": [NotNull],
      "iqmp": [NotNull]
    }
    def loadMembers(self, mappings, maxDepth):
      if not LoadableMembers.loadMembers(self, mappings, maxDepth):
        log.debug('RSA not loaded')
        return False
      return True

  # register to haystack
  model.registerModule(sys.modules[__name__])

--------------------------------------------------------------------------------

As an added bonus, you can also use ctypeslib code generator to autogenerate 
ctypes python structure from C headers.
You should have a look into that : h2py && xml2py 
Anyway, haystack will support you with your generated headers.
Put the generated headers in a separate file, and put your expected values in an
other file. You can then register the autogenerated module.

Example :

  # generated headers are in ctypes_putty_generated.py
  # ctypes_putty.py
  import ctypes_putty_generated as gen

  ################ START copy generated classes ##########################
  # copy generated classes (gen.*) to this module as wrapper
  model.copyGeneratedClasses(gen, sys.modules[__name__])

  # register all classes (gen.*, locally defines, and local duplicates) to haystack
  # create plain old python object from ctypes.Structure's, to picke them
  model.registerModule(sys.modules[__name__])

  RSAKey.expectedValues={
      'bits': [NotNull],
      'bytes': [NotNull],
      'modulus': [NotNull],
      'exponent': [NotNull],
      'private_exponent': [NotNull],
      'p': [NotNull],
      'q': [NotNull],
      'iqmp': [NotNull]
    }
  [... loads of constraints on putty structures... ]

--------------------------------------------------------------------------------

Enjoy.

'''

import ctypes
import logging
from haystack.utils import *

__author__ = "Loic Jaquemet"
__copyright__ = "Copyright (C) 2012 Loic Jaquemet"
__email__ = "loic.jaquemet+python@gmail.com"
__license__ = "GPL"
__maintainer__ = "Loic Jaquemet"
__status__ = "Production"

log = logging.getLogger('model')

# replace c_char_p so we can have our own CString 
if ctypes.c_char_p.__name__ == 'c_char_p':
  ctypes.original_c_char_p = ctypes.c_char_p

# keep orig class and Use our model instead as base Structure class
if ctypes.Structure.__name__ == 'Structure':
  ctypes.original_Structure = ctypes.Structure
if ctypes.Union.__name__ == 'Union':
  ctypes.original_Union = ctypes.Union

def POINTER(cls):
  # check cls as ctypes obj
  if cls is None:
    cls = ctypes.c_ulong
  fake_ptr_base_type = Config.WORDTYPE # 4 or 8 len
  # create object that is a pointer ( see model.isPointer )
  clsname = cls.__name__
  klass = type('haystack.model.LP_%d_%s'%(Config.WORDSIZE, clsname),( Config.WORDTYPE,),{'_subtype_': cls, '_sub_addr_': lambda x: x.value})
  klass._sub_addr_ = property(klass._sub_addr_)
  return klass
ctypes.POINTER = POINTER


# The book registers all haystack modules, and classes, and can keep 
# some pointer refs on memory allocated within special cases...
# see ctypes._pointer_type_cache , _reset_cache()
class _book(object):
  modules = set()
  classes = dict()
  refs = dict()
  def __init__(self):
    pass
  def addModule(self, mod):
    self.modules.add(mod)
  def addClass(self,cls):
    # ctypes._pointer_type_cache is sufficient
    #self.classes[ctypes.POINTER(cls)] = cls
    ctypes.POINTER(cls)
  def addRef(self,obj, typ, addr):
    self.refs[(typ,addr)]=obj
  def getModules(self):
    return set(self.modules)
  def getClasses(self):
    return dict(self.classes)
  def getRef(self,typ,addr):
    #print typ,addr
    #print (typ,addr) in self.refs.keys()
    if len(self.refs) > 35000:
      log.warning('the book is full, you should haystack.model.reset()')
    return self.refs[(typ,addr)]
  def delRef(self,typ,addr):
    del self.refs[(typ,addr)]
  def isRegisteredType(self, typ):
    return typ in self.classes.values()

    
# central model book register
__book = _book()

def reset():
  global __book
  __book.refs = dict()

def getRefs():
  return __book.refs.items()

def printRefs():
  l=[(typ,obj,addr) for ((typ,addr),obj) in __book.refs.items()]
  for i in l:
    print l

def printRefsLite():
  l=[(typ,addr) for ((typ,addr),obj) in __book.refs.items()]
  for i in l:
    print l

def hasRef(typ,origAddr):
  return (typ,origAddr) in __book.refs

def getRef(typ,origAddr):
  if (typ,origAddr) in __book.refs:
    return __book.getRef(typ,origAddr)
  return None

def getRefByAddr(addr):
  ret=[]
  for (typ,origAddr) in __book.refs.keys():
    if origAddr == addr:
      ret.append( (typ, origAddr, __book.refs[(typ, origAddr)] ) )
  return ret

def keepRef(obj,typ=None,origAddr=None):
  ''' Sometypes, your have to cast a c_void_p, You can keep ref in Ctypes object, 
    they might be transient (if obj == somepointer.contents).'''
  # TODO, memory leak for different objects of same size, overlapping struct.
  if (typ,origAddr) in __book.refs:
    # ADDRESS already in refs
    if origAddr is None:
      origAddr='None'
    else:
      origAddr=hex(origAddr)
    if typ is not None:
      log.debug('references already in cache %s/%s'%(typ,origAddr))
    return
  __book.addRef(obj,typ,origAddr)
  return

def delRef(typ,origAddr):
  ''' Forget about a Ref..'''
  if (typ,origAddr) in __book.refs:
    __book.delRef(typ,origAddr)
  return

def get_subtype(cls):
  if hasattr(cls, '_subtype_'):
    return cls._subtype_  
  return cls._type_  


#def register(klass):
#  #klass.classRef = __register
#  #__register[ctypes.POINTER(klass)] = klass
#  __book.addClass(klass)
#  #klass.classRef = __book.classes
#  #klass.classRef = ctypes._pointer_type_cache
#  # p_st._type_
#  return klass

def registeredModules():
  return sys.modules[__name__].__book.getModules()


class CString(ctypes.Union):
  ''' 
  This is our own implementation of a string for ctypes.
  ctypes.c_char_p can not be used for memory parsing, as it tries to load 
  the string itself without checking for pointer validation.
  
  it's basically a Union of a string and a pointer.
  '''
  _fields_=[
  ("string", ctypes.original_c_char_p),
  ("ptr", ctypes.POINTER(ctypes.c_ubyte) )
  ]
  def toString(self):
    if not bool(self.ptr):
      return "<NULLPTR>"
    if hasRef(CString, getaddress(self.ptr)):
      return getRef(CString, getaddress(self.ptr) )
    log.debug('This CString was not in cache - calling toString was not a good idea')
    return self.string
  pass


class NotValid(Exception):
  pass

class LoadException(Exception):
  pass
  

## change LoadableMembers structure given the loaded plugins
import basicmodel
if True:
  import listmodel
  heritance = tuple([listmodel.ListModel,basicmodel.LoadableMembers])
else:
  heritance = tuple([basicmodel.LoadableMembers])
LoadableMembers = type('LoadableMembers', heritance, {})

class LoadableMembersUnion(ctypes.Union, LoadableMembers):
  pass
class LoadableMembersStructure(ctypes.Structure, LoadableMembers):
  pass

import inspect,sys

def copyGeneratedClasses(src, dst):
  ''' 
    Copies the members of a generated module into a classic module.
    Name convention : 
    generated: ctypes_libraryname_generated.py
    classic  : ctypes_libraryname.py
    
  :param me : dst module
  :param src : src module, generated
  '''
  __root_module_name,__dot,__module_name = dst.__name__.rpartition('.')
  _loaded=0
  _registered=0
  for (name, klass) in inspect.getmembers(src, inspect.isclass):
    if issubclass(klass, LoadableMembers): 
      if klass.__module__.endswith('%s_generated'%(__module_name) ) :
        setattr(dst, name, klass)
        _loaded+=1
    else:
      #log.debug("%s - %s"%(name, klass))
      pass
  log.debug('loaded %d C structs from %s structs'%( _loaded, src.__name__))
  log.debug('registered %d Pointers types'%( _registered))
  log.debug('There is %d members in %s'%(len(src.__dict__), src.__name__))
  return 


def createPOPOClasses( targetmodule ):
  ''' Load all model classes and create a similar non-ctypes Python class  
    thoses will be used to translate non pickable ctypes into POPOs.
  '''
  _created=0
  for klass,typ in inspect.getmembers(targetmodule, inspect.isclass):
    if typ.__module__.startswith(targetmodule.__name__):
      kpy = type('%s.%s_py'%(targetmodule.__name__, klass),( basicmodel.pyObj ,),{})
      # add the structure size to the class
      #if type(typ) == type(LoadableMembers) or type(typ) == type( ctypes.Union) :
#      if type(typ) == type(LoadableMembersStructure) or type(typ) == type( ctypes.Union) :
      if issubclass(typ, LoadableMembers ) :
        setattr(kpy, '_len_',ctypes.sizeof(typ) )
      else:
        setattr(kpy, '_len_', None )
      # we have to keep a local (model) ref because the class is being created here.
      # and we have a targetmodule ref. because it's asked.
      # and another ref on the real module for the basic type, because, that is probably were it's gonna be used.
      setattr(sys.modules[__name__], '%s.%s_py'%(targetmodule.__name__, klass), kpy )
      #setattr(sys.modules[__name__], '%s_py'%(klass), kpy )
      setattr(targetmodule, '%s_py'%(klass), kpy )
      _created+=1
      if typ.__module__ != targetmodule.__name__: # copy also to generated
        setattr(sys.modules[typ.__module__], '%s_py'%(klass), kpy )
        #log.debug("Created %s_py"%klass)
  log.debug('created %d POPO types'%( _created))
  return

def registerModule( targetmodule ):
  ''' 
  Registers a ctypes module. To be run by target module.
  
  All members in this module will be registered, against their pointer types,
  in a lookup table.
  
  Creates POPO's to be able to unpickle ctypes.
  '''
  if targetmodule in registeredModules():
    log.warning('Module %s already registered. Skipping.'%(targetmodule))
    return
  _registered = 0
  for klass,typ in inspect.getmembers(targetmodule, inspect.isclass):
    if typ.__module__.startswith(targetmodule.__name__) and issubclass(typ, ctypes.Structure):
      #register( typ )
      _registered += 1
  # create POPO's
  createPOPOClasses( targetmodule )
  __book.addModule(targetmodule)
  log.debug('registered %d types'%( _registered))
  log.debug('registered %d module total'%(len(__book.getModules())))
  return

def isRegistered(cls):
  #return cls in sys.modules[__name__].__dict__.values()
  return __book.isRegisteredType(cls)

# create local POPO ( lodableMembers )
#createPOPOClasses(sys.modules[__name__] )
LoadableMembersStructure_py = type('%s.%s_py'%(__name__, LoadableMembersStructure),( basicmodel.pyObj ,),{})
LoadableMembersUnion_py = type('%s.%s_py'%(__name__, LoadableMembersUnion),( basicmodel.pyObj ,),{})
# register LoadableMembers 
#### FIXME DELETE register(LoadableMembersStructure)


# replace c_char_p - it can handle memory parsing without reading it 
if ctypes.c_char_p.__name__ == 'c_char_p':
  ctypes.c_char_p = CString

# switch class - we need our methods on ctypes.Structures for generated classes to work  
if ctypes.Structure.__name__ == 'Structure':
  ctypes.Structure = LoadableMembersStructure
if ctypes.Union.__name__ == 'Union':
  ctypes.Union = LoadableMembersUnion

