#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2011 Loic Jaquemet loic.jaquemet+python@gmail.com
#

import logging
import argparse, os, pickle, time, sys
import re
import struct
import ctypes
import array
import itertools
import numbers

from utils import xrange
import memory_dumper
import signature 

log = logging.getLogger('pattern')

OUTPUTDIR='../outputs/'

def make(opts):
  log.info('Make the signature.')
  ppMapper = PinnedPointersMapper()  
  for dumpfile in opts.dumpfiles:
    sig = Signature.fromDumpfile(dumpfile)
    log.info('pinning offset list created for heap %s.'%(sig))
    ppMapper.addSignature(sig)
    
  log.info('Find similar vectors between pointers on all signatures.')
  ppMapper.run()
  #reportCacheValues(ppMapper.cacheValues2)
  #saveIdea(opts, 'idea2', ppMapper.cacheValues2)

  ## we have :
  ##  resolved PinnedPointers on all sigs in ppMapper.resolved
  ##  unresolved PP in ppMapper.unresolved
  
  ## next step
  log.info('Pin resolved PinnedPointers to their respective heap.')



class Signature:
  ''' 
  Wrapper object the list of intervals between pointers identified in the dumpfile.
  When the memory is :
  P....P..P.PPP.PP.PPPP.PPP.P..P..................P
  with P being a Word of 4 bytes which value could be a pointer value.
  The signature is 
  [20,12,8,4,4,8,4,8,4,4,4,8,4,4,8,12,80]
  
  It abstracts the memory contents to its signature.
  '''
  def __init__(self, mmap=None, dumpFilename=None):
    self.mmap = mmap  
    self.dumpFilename = dumpFilename
    self.name = os.path.basename(dumpFilename)
    self.addressCache = {}

  def _getDump(self):
    #log.info('Loading the mappings in the memory dump file.')
    mappings = memory_dumper.load( file(self.dumpFilename,'r'), lazy=True)
    heap = None
    if len(mappings) > 1:
      heap = [m for m in mappings if m.pathname == '[heap]'][0]
    if heap is None:
      raise ValueError('No [heap]')
    self.mmap = heap
    return

  def _load(self):
    ## DO NOT SORT LIST. c'est des sequences. pas des sets.
    myname = self.dumpFilename+'.pinned'
    if os.access(myname,os.F_OK):
      # load
      f = file(myname,'r')
      nb = os.path.getsize(f.name)/4 # simple
      sig = array.array('L')
      sig.fromfile(f,nb)
      log.debug("%d Signature intervals loaded from cache."%( len(sig) ))
    else:
      log.info("Signature has to be calculated for %s. It's gonna take a while."%(self.name))
      pointerSearcher = signature.PointerSearcher(self.mmap)
      self.WORDSIZE = pointerSearcher.WORDSIZE
      sig = array.array('L')
      # save first offset
      last = self.mmap.start
      for i in pointerSearcher: #returns the vaddr
        sig.append(i-last) # save intervals between pointers
        #print hex(i), 'value:', hex(self.mmap.readWord(i) )
        last=i
      sig.tofile(file(myname,'w'))
    self.sig = sig
    #
    self.addressCache[0] = self.mmap.start # previous pointer of interval 0 is start of mmap
    self._loadAddressCache()
    return

  def _loadAddressCache(self):
    ## DO NOT SORT LIST. c'est des sequences. pas des sets.
    myname = self.dumpFilename+'.pinned.vaddr'
    if os.access(myname,os.F_OK):
      addressCache = pickle.load(file(myname,'r'))
      log.debug("%d Signature addresses loaded from cache."%( len(addressCache) ))
      self.addressCache.update(addressCache)
    else: # get at least 10 values
      for i in xrange(0, len(self), len(self)/10):
        self.getAddressForPreviousPointer(i)
      self._saveAddressCache()
    return

  def _saveAddressCache(self):
    myname = self.dumpFilename+'.pinned.vaddr'
    pickle.dump(self.addressCache, file(myname,'w'))

  def getAddressForPreviousPointer(self, offset):
    ''' 
    sum all intervals upto the offset. that give us the relative offset.
    add to dump.start , and we have the vaddr
    We need to sum all up to offset not included.
    it we include the offset, we get the second pointer vaddr.
    '''
    # use cache my friends
    if offset in self.addressCache :
      return self.addressCache[offset]
    # get closest one
    keys = sorted(self.addressCache)
    keys = list(itertools.takewhile(lambda x: x < offset, keys)) 
    last = keys[-1] # take the closest
    startValue = self.addressCache[last] ## == addr(last-1)
    subseq = self.sig[last:offset] # we are not interested in adding offset interval. that would give us the second pointer address 
    #newsum = startValue + reduce(lambda x,y: x+y, subseq)
    #self.addressCache[offset] = newsum
    ## be proactive +/- 40 Mo
    newsum = startValue
    for i in range(last, offset):
      newsum+=self.sig[i]
      self.addressCache[i+1] = newsum
    ## be proactive
    return newsum

  def __len__(self):
    return len(self.sig)
  def __str__(self):
    return "<Signature '%s'>"%(self.name)
  @classmethod
  def fromDumpfile(cls, dumpfile):
    inst = Signature(dumpFilename = dumpfile.name)
    inst._getDump()
    inst._load()
    return inst

class Sequences:
  ''' 
  Builds a list of sequences of interval for each interval in the signature.
  [2,3,3,4,5,1,2,3,4,5] gives
  [(2,3,3), (3,3,4), (3,4,5), (4,5,1), (5,1,2), (1,2,3), (2,3,4), (3,4,5)] 
  
  '''
  def __init__(self, sig, size, cacheAll=True):
    self.size = size
    self.signature = sig
    self.sig = sig.sig
    self.sets={} # key is sequence len
    self.cacheAll=cacheAll
    self.findUniqueSequences(self.sig)
          
  def findUniqueSequences(self, sig):
    log.debug('number of intervals: %d'%(len(sig)))
    sig_set = set(sig)
    log.debug('number of unique intervals value: %d'%(len(sig_set)) )
    # create the tuple      
    self.sets[self.size] = set(self.getSeqs())
    log.debug('number of unique sequence len %d : %d'%(self.size, len(self.sets[self.size])))
    return
  
  def getSeqs(self):
    if not hasattr(self, 'seqs'):
      seqlen = self.size
      self.seqs =  [ tuple(self.sig[i:i+seqlen]) for i in xrange(0, len(self.sig)-seqlen+1) ]
      seqs =  self.seqs
      return seqs
  def __len__(self):
    return len(self.signature)-self.size
    
  def __iter__(self):
    seqlen = self.size
    for i in xrange(0, len(self.sig)-seqlen+1):
      yield tuple(self.sig[i:i+seqlen])
    return


class PinnedPointers:
  '''
    A variable length sequence of intervals between pointers.
    It already pinned at a specific offset of a signature, 
    so you migth find several instance p1 and p2 at different offset, but with the same sequence
    and therefore equal signature. p1 == p2.
    It is easily pin onto the initial dump/heap by getAddress()
    
    @param sequence: the sequence of intervals between pointers
    @param sig: the whole signature object linked back to the memoryMap
    @param offset: the offset of this interval within the signature 
  '''
  def __init__(self, sequence, sig, offset):
    self.sequence = sequence
    self.nb_bytes = sum(sequence) +  signature.PointerSearcher.WORDSIZE # add wordSIZE
    self.offset = offset
    self.sig = sig 
    self.relations = {}
    self.vaddr = None

  def pinned(self, nb=None):
    if nb is None:
      nb == len(self.sequence)
    return self.sequence[:nb]

  def __len__(self):
    return len(self.sequence) 

  def structLen(self):
    return self.nb_bytes

  def __cmp__(self,o):
    if len(self) != len(o):
      return cmp(len(self),len(o) )
    if self.structLen() != o.structLen(): # that means the sequence is different too
      return cmp(self.structLen(), o.structLen())
    if self.sequence != o.sequence: # the structLen can be the same..
      return cmp(self.sequence, o.sequence)
    #else offset is totally useless, we have a match
    return 0

  def __contains__(self,other):
    raise NotImplementedError
    if not isinstance(other, PinnedPointers):
      raise ValueError
    if other.sig == self.sig: ## well, not really
      if other.offset >= self.offset and other.offset <= self.offset+len(self) :
        #if other.sequence in self.sequence: ## need subsearch
        return True
    return False

  def addRelated(self,other, sig=None):
    ''' add a similar PinnedPointer from another offset or another sig '''
    if self != other:
      raise ValueError('We are not related PinnedPointers.')
    if sig is None:
      sig = self.sig
    if sig not in self.relations:
      self.relations[sig] = list()
    self.relations[sig].append( other )
    return

  def getAddress(self, numOffset=0):
    ''' 
    return the vaddr of pointer <numOffset>. 
      by default numOffset == 0 , returns the vaddr of the first interval 
      ( that migth be the first or second pointer in the struct )
    '''
    if self.vaddr is None:
      if numOffset >= len(self.sequence):
        raise IndexError
      self.vaddr = self.sig.getAddressForPreviousPointer(self.offset)
    if numOffset != 0:
      return self.sig.getAddressForPreviousPointer(self.offset+numOffset)
    return self.vaddr

  def __str__(self):
    return '<PinnedPointers %s[%d:%d] +%d bytes/%d pointers>' %( self.sig, self.offset,self.offset+len(self), self.nb_bytes, len(self.sequence)+1 )

  @classmethod
  def link(cls, lstOfPinned):
    for i,p1 in enumerate(lstOfPinned):
      for p2 in lstOfPinned[i+1:]:
        p1.addRelated(p2,p2.sig)
        p2.addRelated(p1,p1.sig)
    return



class Dummy(object):
  pass

class AnonymousStructRange:
  '''
  Map a pinnedPointer sequence/signature onto a specific memory at a specific offset.
  We are now able to query the structure contents.
  
  Operators:
    __contains__ : if applied by a Number, it will be understoof as a memory address.
                  if the memory addres is in range of this structure, return True.
                  in all other cases, return False
    __cmp__ : if applied by a Number, it will be understoof as a memory address.
                  if the memory address is in range of this structure, return 0.
                  in all other cases, return the __cmp__ of the address compared to the start of the struct
  '''
  def __init__(self, pinnedPointer):
    self.pinnedPointer = pinnedPointer
    self.start = pinnedPointer.getAddress() # by default we start at the first pointer
    self.stop = pinnedPointer.getAddress(len(pinnedPointer)) # by default we stop at the last pointer
    self.stop += signature.PointerSearcher.WORDSIZE # add the length of the last pointer
    self.pointers = None 
    self.pointersTypes = {}
    self.pointersValues = None 
    self.typename = self.makeTypeName()
    
  def getPointersAddr(self):
    if self.pointers is None:
      self.pointers = [self.pinnedPointer.getAddress(i) for i in range(len(self.pinnedPointer)+1 ) ]
    return self.pointers

  def getPointersValues(self):
    if self.pointersValues is None:
      self.pointersValues = [self.pinnedPointer.sig.mmap.readWord( addr) for addr in self.getPointersAddr()]
    return self.pointersValues
  
  def setPointerType(self, number, anonStruct):
    ''' set a specific pointer to a specific anonStruct type '''
    if anonStruct.sig() != self.sig():
      raise TypeError('You cant type with a AnonStruct from another Signature. %s vs %s'%(self,anonStruct))
    if number in self.pointersTypes:
      raise IndexError('Pointer number %d has already been identified as a type %s - new type : %s'%( 
                        number, self.getPointerType(number).type(), anonStruct.type() ) )
    self.pointersTypes[number] = anonStruct
    myself=''
    if self == anonStruct:
      myself=' (MYSELF) '
    log.debug('Set %s pointer number %d to type %s %s'%(self.type(), number, self.getPointerType(number).type(), myself ))
    return
  
  def getPointerType(self, number):
    return self.pointersTypes[number]
  
  def sig(self):
    return self.pinnedPointer.sig
    
  def type(self):
    return self.typename
  
  def __contains__(self,other):
    if isinstance(other, numbers.Number):
      rel = other - self.start
      if rel > len(self) or ( rel < 0 ):
        return False
      return True
    else:
      return False
      
  def __cmp__(self, other):
    if other in self:
      return 0
    else:
      return cmp(self.start, other)

  def __len__(self):
    return int(self.stop-self.start) 

  def makeTypeName(self):
    return 'AnonStruct_%s_%s_%s_%s'%(len(self), len(self.pinnedPointer), self.pinnedPointer.sig.name, self.pinnedPointer.offset )

  def toCtypesString(self):
    s=''
    return      

  def __str__(self):
    return '<%s>'%(self.type())

class PinnedPointersMapper:
  '''
  a) On identifie les sequences d'intervalles longues ( taille fixe a 20 ).
  b) on trouve les sequences communes a toutes les signatures.
  c) pour chaque offset de chaque signature, on determine un PinnedPointer
      qui couvre la plus grande sequence composee de sequence communes.
   *** Erreur possible: la sequence creee en sig1 n'existe pas en sig2.
          cas possible si sig2 contient A4 et A5 en deux zones distinces ( A5 == A4[1:]+...
          et si sig 1 contient A4A5 en une zone distincte 
          on se retrouve avec sig A4A5 mais sig2.A4 et sig2.A5
          on peut dans ce cas, redecouper sig1 selon le plus petit denominateur commun de sig2
     -> check routine
  d) on linke ces PP entres elles ( central repo serait mieux )
  e) Meta info: on trouve les multiple instances ( same struct, multiple alloc)
  '''
  def __init__(self, sequenceLength=20):
    self.cacheValues2 = {}
    self.signatures = []
    self.signatures_sequences = {}
    self.started = False
    self.common = []
    self.length = sequenceLength
    return
  
  def addSignature(self, sig):
    if self.started:
      raise ValueError("Mapping has stated you can't add new signatures")
    self.signatures.append(sig)
    return
    
  def _findCommonSequences(self):
    log.info('Looking for common sequence of length %d'%(self.length))
    common = None
    # make len(sig) sub sequences of size <length> ( in .sets )
    for sig in self.signatures:
      self.signatures_sequences[sig] = Sequences(sig, self.length, False)
      if common is None:
        common = set(self.signatures_sequences[sig].sets[self.length])
      else:
        common &= self.signatures_sequences[sig].sets[self.length]
    log.info('Common sequence of length %d: %d seqs'%(self.length, len(common)))
    return common  
  
  def _mapToSignature(self, sig ):    
    # maintenant il faut mapper le common set sur l'array original, 
    # a) on peut iter(sig) jusqu'a trouver une sequence non common.
    # b) reduce previous slices to 1 bigger sequence. 
    # On peut aggreger les offsets, tant que la sequence start:start+<length> est dans common.
    # on recupere un 'petit' nombre de sequence assez larges, censees etre communes.
    sig_aggregated_seqs = []
    sig_uncommon_slice_offset = []
    start = 0
    stop = 0
    i=0
    length = self.length
    seqs_sig1 = self.signatures_sequences[sig]
    common = self.common
    enum_seqs_sig = enumerate(seqs_sig1) # all subsequences, offset by offset
    try:
      while i < len(seqs_sig1): # we wont have a StopIteration...
        for i, subseq in enum_seqs_sig:
          if subseq in common:
            start = i
            #log.debug('Saving a Uncommon slice %d-%d'%(stop,start))
            sig_uncommon_slice_offset.append( (stop,start) )
            break
          del subseq
        # enum is on first valid sequence of <length> intervals
        #log.debug('Found next valid sequence at interval offset %d/%d/%d'%(i,len(sig.sig), len(seqs_sig1) ))
        for i, subseq in enum_seqs_sig:
          if subseq in common:
            del subseq
            continue
          else: # the last interval in the tuple of <length> intervals is not common
            # so we need to aggregate from [start:stop+length]
            # there CAN be another common slice starting between stop and stop+length.
            # (1,2,3,4) is common , (1,2,3,4,6) is NOT common because of the 1, (2,3,4,6) is common.
            # next valid slice is at start+1 
            # so Yes, we can have recovering Sequences
            stop = i # end aggregation slice
            seqStop = stop+length-1
            pp = savePinned(self.cacheValues2, sig, start, seqStop-start) # we should also pin it in sig2, sig3, and relate to that...
            sig_aggregated_seqs.append( pp ) # save a big sequence
            #log.debug('Saving an aggregated sequence %d-%d'%(start, stop))
            del subseq
            break # goto search next common
        # find next valid interval
      # wait for end of enum
    except StopIteration,e:
      pass
    #done
    #log.debug('%s'%sig1_uncommon_slice_offset)
    log.info('There is %d uncommon slice zones in %s'%( len (sig_uncommon_slice_offset), sig) )
    log.info('There is %d common aggregated sequences == struct types in %s'%( len(sig_aggregated_seqs), sig))

    return sig_uncommon_slice_offset, sig_aggregated_seqs
  
  def _findMultipleInstances(self):
    import itertools
    allpp = sorted([v for l in self.cacheValues2.values() for v in l], reverse=True)
    unresolved = []
    linkedPP  = []
    linked = 0
    multiple = 0
    
    for k, g in itertools.groupby( allpp ):
      l = list(g)
      if len(l) < len(mapper.signatures): # we can have multiple instances btu not less.
        unresolved.extend(l)
        #print 'not same numbers'
        continue
      else:
        allSigs = True
        # we should have all 3 signatures
        found = [pp.sig for pp in l ]
        for s in mapper.signatures:
          if s not in found:
            unresolved.extend(l)
            #print 'not same sigs', s
            allSigs = False
            break
        # if ok, link them all
        if allSigs:
          PinnedPointers.link(l)
          linkedPP.extend(l)
          multiple+=1
          linked+=len(l)
          
    unresolved = sorted(unresolved,reverse=True)
    linkedPP = sorted(linkedPP,reverse=True)
    
    self.unresolved = unresolved
    self.resolved = linkedPP
    log.info('Linked %d PinnedPointers across all Signatures, %d unique in all Signatures '%(linked, multiple))
    log.info('left with %d/%d partially unresolved pp'%(len(unresolved), len(allpp) ) )
    #cache to disk
    #cacheToDisk(self.resolved,'pinned-resolved')
    #cacheToDisk(self.unresolved,'pinned-unresolved')
    return
  

  def run(self): 
    self.started = True
    all_common_pp = []
    
    CACHE='pinned-resolved'
    CACHE2='pinned-unresolved'
    global mapper
    mapper = self
    
    ### drop 1 : find common sequences
    self.common = self._findCommonSequences()
      
    ### drop 2: Map sequence to signature, and aggregate overlapping sequences.
    for sig in self.signatures:
      unknown_slices, common_pp = self._mapToSignature(sig ) 
      all_common_pp.extend(common_pp)

    ### drop 3: error case, we have been too optimistic about unicity of common sequence.
    ###   lets try and reduce the errors.
    ### for each structLen, find at least one pp for each sig
    
    ### chance are that only the last interval is botched, so we only have to compare between
    ### pp1.sequence[:-1] and pp2.sequence[:-1] to find a perfect match
    # we nee to find sole pointer. pop all equals in the 3 sigs.
    ### drop 3: Analyze and find multiple instances of the same Sequence
    self._findMultipleInstances()
      
    ### drop 4: Sequence should have been linked, cross-signature. Try to extend them
    ### On peut pas agrandir les sequences. il n"y a plus de common pattern,
    ### Par contre, on peut essayer de trouver des sequences plus courtes dans les
    ### intervalles uncommon_slices
    self._pinResolved()
    return 


  ##################################3 STEP 2 , pin them on the wall/heap
    
  def _pinResolved(self ):
    caches = {}
    for sig in self.signatures[:]:
      a = Dummy()
      resolved_for_sig = [ pp for pp in self.resolved if pp.sig == sig ]
      unresolved_for_sig = [ pp for pp in self.unresolved if pp.sig == sig ]
      log.debug('Pin anonymous structures on %s'%(sig))
      pinned = [AnonymousStructRange(pp) for pp in resolved_for_sig]
      log.debug('Create list of structures addresses for %s'%(sig) )
      pinned_start = sorted([pp.getAddress() for pp in resolved_for_sig])
      log.debug('Pin probable anonymous structures on %s'%(sig) )
      pinned_lightly = [AnonymousStructRange(pp) for pp in unresolved_for_sig]
      log.debug('Create list of probable structures addresses for %s'%(sig) )
      pinned_lightly_start = sorted([pp.getAddress() for pp in unresolved_for_sig])
      # save it
      a.pinned = pinned
      a.pinned_start = pinned_start
      a.pinned_lightly = pinned_lightly
      a.pinned_lightly_start = pinned_lightly_start
      caches[sig] = a
      
    #log.debug('Overlapping sequences can happen. we will filter them later using a tree of structures.')
    #for i, pp in enumerate(pinned):
    #  if pp.start in pinned[i+1:]:
    #    pass
    
    ## TODO stack pointers value and compare them to pinned_start, pinned_lightly_start
    
    # In each anon structure Pa, get each pointers value.
    # If the value is in the list of structures head addresses, we have a start of struct (mostly true)
    #   we check Related Struct in the other signatures to see if everybody agrees.
    #  the parent in sig A (Pa) should point to children type in sig A (Ca)
    #  the parent in sig B (Pb) should point to children type in sig B (Cb)
    # Pa and Pb are related, Ca and Cb should be related too.
    sig = self.signatures[0]
    pinned = caches[sig].pinned
    pinned_start = caches[sig].pinned_start
    pinned_lightly = caches[sig].pinned_lightly
    pinned_lightly_start = caches[sig].pinned_lightly_start
    ## for as in pinned, get pointers values and make a tree
    log.debug('Going through pointers')
    startsWithPointer = 0
    startsMaybeWithPointer = 0
    pointsToStruct = 0
    pointsToStruct2 = 0
    self.startTree = []
    self.startTree2 = []
    self.tree = []
    self.tree2 = []
    startsWithPointerList = self.startTree
    startsMaybeWithPointerList = self.startTree2
    pointsToStructList = self.tree
    pointsToStructList2 = self.tree2
    for i,ap in enumerate(pinned):
      pts = ap.getPointersValues()
      me = ap.pinnedPointer.getAddress()
      for j,ptr in enumerate(pts): ## ptr is the value of pointer number j in the anonymoustruct ap
        sub = pinned_start#[i+1:]
        log.debug('--------------------------------------------------------------------------')        
        if ptr in sub:
          log.debug('Lucky guess s:%d, p:%d, we find a pointer to the start of %d PinnedPointer struct.'%(i, j, sub.count(ptr))) 
          startsWithPointer+=1
          startsWithPointerList.append((ap,j))
          # check if the same struct in sig2, sig3... points to the same target struct
          if self._checkRelations(caches, ap, j, ptr):
            log.info('ID-ed %s.pointers[%d] to type %s'%(ap, j, ap.getPointerType(j)) )
          
          # probably else:
        elif ptr in pinned_lightly_start:
          sub = pinned_lightly_start#[i+1:]
          log.debug('Lucky guess s:%d, p:%d we find a pointer to %d maybe-PinnedPointer struct.'%(i, j, sub.count(ptr)))
          startsMaybeWithPointer+=1
          startsMaybeWithPointerList.append((ap,j))
          # probably else:
        elif ptr in pinned:  #### ptr is not the start of a anonymous struct
          sub = pinned
          #log.debug('normal guess s:%d, p:%d, we find a pointer to the start or CONTENT of %d PinnedPointer struct. %x'%(i, j, sub.count(ptr), ptr))
          pointsToStruct+=1
          pointsToStructList.append((ap,j))
        elif ptr in pinned_lightly:
          sub = pinned_lightly
          #log.debug('normal guess s:%d, p:%d, we find a pointer to the start or CONTENT of %d PinnedPointer MAYBE-struct.'%(i, j, sub.count(ptr)))
          pointsToStruct2+=1
          pointsToStructList2.append((ap,j))
        else:
          #log.debug('That pointer s:%d, p:%d is lost in the void..'%(i, j))
          # check nearest in pinned
          first_addr,anonStruct = _findFirstStruct(ptr, pinned_start, pinned)
          # check in lightly
          first_addr_l,anonStruct_l = _findFirstStruct(ptr, pinned_lightly_start, pinned_lightly)
          if first_addr > first_addr_l:
            s = 'pinned'
          else:
            s = 'pinned_lightly'
            anonStruct = anonStruct_l
          #log.debug('Nearest struct is at %d bytes in %s'%( min(first_addr_l, first_addr_l)-ptr , s))
          offset = anonStruct.start-ptr
          if  offset < 64: # TODO: test if addr not int another struct
            log.info('Found a probable start of struct at %d bytes earlier'%(offset))
        log.debug('--------------------------------------------------------------------------')          
          
    # pointer to self means c++ object ?
    sig._saveAddressCache()

    log.debug('We have found %d pointers to pinned structs'%(startsWithPointer))
    log.debug('We have found %d pointers to pinned maybe-structs'%(startsMaybeWithPointer))
    return

  def _findFirstStruct(self, ptr, addresses, anons):
    try:
      first_addr = itertools.dropwhile(lambda x: x < ptr, addresses).next()
      anon = anons[addresses.index(first_addr)] # same index
    except StopIteration,e:
      return -1,None
    return first_addr,anon

  def _checkRelations(self, cache, ap, pointerIndex, ptr ) :
    '''
      go through all related pinned pointers of the other signatures.
      check if the targeted pinnedpointer for the pointer number <pointerIndex> is the same pinnedPointer
      than in the sig1.
      if its not, find in the other signatures, what is the target struct.
      
      @param cache: cache for all calculated lists
      @param ap: the PinnedPointer sequence 
      @param pointerIndex: the index number for the ptr
      @param ptr: ptr is the value of pointer number pointerIndex 
    '''
    pp = ap.pinnedPointer
    ok = False
    mypinned = cache[pp.sig].pinned
    mypinned_start = cache[pp.sig].pinned_start
    # reverse found a anonstruct covering this ptr value ( start or middle )
    anontargetPP = mypinned[mypinned.index(ptr)] 
    # reverse found a anonstruct covering this ptr value ( start ONLY )
    #anontargetPP = mypinned[mypinned_start.index(ptr)] 
    log.debug('anontargetPP is %s'%anontargetPP)
    targetPP = anontargetPP.pinnedPointer
    perfect = [(ap, anontargetPP)] # get ourselves
    
    
    # look in other signatures
    for sig in self.signatures:
      if sig == pp.sig:
        continue
      ok = False
      
      ## 1 - take the related PinnedPointer from the next signature to the parent PP of our first signature
      ##     and calculate the value of the n-th pointer in that pp for that signature.
      relatedPPs = pp.relations[sig]              #parent struct
      if len(relatedPPs) > 1: 
        log.debug('We have more than one relatedPP to target')
      tgtAnons = [AnonymousStructRange(relatedPP) for relatedPP in relatedPPs]
      tgtPtrs = [tgtAnon.getPointersValues()[pointerIndex] for tgtAnon in tgtAnons]

      ## 2 - take the related PinnedPointer from the next signature to [the n-th pointer/children PP of our first signature]
      ##     if we find one start address that is equal to the previously calculated pointer value 
      ##     that means we find a parent-children match in both parent types and children types. 
      relatedTargetPPs = targetPP.relations[sig]  #children struct
      for relatedTargetPP in relatedTargetPPs:
        addr = AnonymousStructRange(relatedTargetPP).start
        log.debug('compare %d and %s'%(addr,tgtPtrs))
        if addr in tgtPtrs:
          log.debug('** found a perfect match between %s and %s'%(pp.sig, relatedTargetPP.sig))
          ok = True
          ## on type tous les pointers possible, puis on fera des stats sur le ap
          _anon_parent = tgtAnons[tgtPtrs.index(addr)]  # TODO border case, multiple struct pointing to the same child
          _parentStart = _anon_parent.start
          parent = cache[sig].pinned[cache[sig].pinned_start.index(_parentStart)]          
          child = cache[sig].pinned[cache[sig].pinned_start.index(addr)]
          perfect.append((parent, child ))
          break

      ## not ok, we did not find a related match on first offset of pinneddpointer.
      ## that means the targeted struct is either:
      ##   a) not starting with a pointer ( source pointer points before the target pinnedpointer)
      ##        which is weird because, if sig1 if ok, sigX should be ok too.
      ##   b) a bad aggregation has taken place in the target signature. target PP is too big
      ##        maybe we can cut it in halves ?
      ##   c) the pointer stills points to nowhere. we can't be sure of anything
      if not ok:
        ok2 = False
        for tgtPtr in tgtPtrs:
          #log.debug('NOT found a match between %s and %s'%(pp.sig, relatedTargetPP.sig))
          sub = cache[sig].pinned
          if tgtPtr in sub:
            afound = sub[sub.index(tgtPtr)]
            found = afound.pinnedPointer
            log.info('Found %d content-pointed struct (not start) in %s'%(sub.count(tgtPtr), sig))
            log.info('   source pp was  %s'%(pp))
            for myrelatedPP in relatedPPs:
              log.info('   source related pp was  %s'%(myrelatedPP))
            log.info('   -- got %s (0x%x)'%( found, tgtPtr-found.getAddress()))
            sameseq = False
            # get start == tgtpp.getAddress(n) , and comp tgtpp.sequence[n:n+len]
            log.info('   source target pp was  %s (same seq == %s)'%(targetPP, sameseq))
            for mytargetPPrelated in relatedTargetPPs:
              log.info("   source's target's related pp was  %s (0x%x)"%(mytargetPPrelated, tgtPtr-mytargetPPrelated.getAddress())) 
            ## we now know that type(found) should be == type(targetPP)
            ## can we recalculate found and targetPP so they will be related ?
            ## what to do with related pps of targetPP ? they can be multiple instance....
            ## even then, there status of related to targetPP must be severed. we have proof
            ## they are not the precise instance we are looking for.
            seq1 = targetPP
            ok2 = True
            break
          elif tgtPtr in cache[sig].pinned_lightly:
            sub = cache[sig].pinned_lightly
            afound = sub[sub.index(tgtPtr)]
            found = afound.pinnedPointer
            log.info('Found %d pointed struct in LIGHTLY %s'%(sub.count(tgtPtr), sig))
            log.info('   source pp was  %s'%(pp))
            for myrelatedPP in relatedPPs:
              log.info('   source related pp was  %s'%(myrelatedPP))
            log.info('   source target pp was  %s'%(targetPP))
            for mytargetPPrelated in relatedTargetPPs:
              log.info("   source's target's related pp was  %s"%(mytargetPPrelated)) 
            log.info('   got %s'%( found))

            ok2 = True
            break
        if not ok2:
            log.info('This one does not points anywhere to a common pinnedPointer struct  %s'%(sig))
            break

    # all sig have been parsed and we found a type(parent->children_in_pos_x) identical for all parent
    if ok and len(perfect) == len(self.signatures):
      ## save that as a perfect match
      ## pp and relatedPP and be Id equals.
      ## targetPP and all perfect[] can be id equals.
      for parent, child in perfect:
        _mysig = parent.pinnedPointer.sig
        parent.setPointerType( pointerIndex, child)
      return True
    return False
  


def t(mapper):

  for k,v in mapper.cacheValues2.items():
    # we have a list of x pp
    if len(v) == len(mapper.signatures):
      # we should have all 3 signatures
      found = [pp.sig for pp in v ]
      for s in mapper.signatures:
        if s not in [found]:
          print '%s not in found'%(s) 
      

def cacheExists(name):
  return os.access(os.path.sep.join([OUTPUTDIR,name]),os.F_OK)
  
def cacheLoad(name):
  log.debug('use cache for %s'%(name))
  return pickle.load(file(os.path.sep.join([OUTPUTDIR,name]),'r'))

def cacheToDisk(obj, name):
  log.debug('save to cache for %s'%(name))
  pickle.dump(obj, file(os.path.sep.join([OUTPUTDIR,name]),'w'))

  
def idea1(sig1, sig2, stepCb):
  '''
    on a pinner chaque possible pointeur vis a vis de sa position relative au precedent pointer.
    Si une structure contient deux ou plus pointers, on devrait donc retrouver 
    une sequence de position relative comparable entre heap 1 et heap 2.
    On a donc reussi a pinner une structure.
    
    modulo les pointer false positive, sur un nombre important de dump, on degage
    des probabilites importantes de detection.
  '''
  step = len(sig1)/100
  log.info('looking for related pointers subsequences between heap1(%d) and heap2(%d)'%(len(sig1),len(sig2)))
  cacheValues1 = {}
  # first pinning between pointer 1 value and pointer value 2
  for offset1 in prioritizeOffsets(sig1): #xrange(0, len(sig1)): # do a non linear search
    if (offset1 % step) == 0:
      stepCb(offset1, cacheValues1)
    # TODO : if value1 in cache, copy Pinned Offsets to new sig1 offset and continue
    # please cache res for value1
    value1 = sig1[offset1]
    offset2=-1
    while True:
      try:
        offset2 += 1
        offset2 = sig2.index(value1, offset2)
      except ValueError,e:
        log.debug('no more value1(%d) in sig2, goto next value1'%(value1))
        break # goto next value1
      # on check le prefix sequence commune la plus longue
      off1 = offset1+1
      off2 = offset2+1
      match_len = 1 # match_len de 1 are interesting, to be validated against content... + empiric multiple dumps measurements
      try:
        while sig1[off1] == sig2[off2]:
          off1 += 1
          off2 += 1
          match_len+=1
        # not equals
        #log.debug('Match stop - Pinned on %d intervals (first %d)'%(match_len, value1))
        saveSequence(value1, cacheValues1, sig1, offset1, sig2, offset2, match_len)
      except IndexError, e: # boundary stop, we should have a pretty nice pinning here
        log.debug('Boundary stop - Pinned on %d intervals'%(match_len))
        saveSequence(value1, cacheValues1, sig1, offset1, sig2, offset2, match_len)
      pass # continue next offset2 for value1
    #
    pass  # continue next value1
  #
  return cacheValues1

def prioritizeOffsets(sig):
  indexes = []
  log.debug('Prioritize large intervals.')
  for val in sorted(set(sig), reverse=True): # take big intervals first
    tmp = []
    i = 0
    while True:
      try:
        i = sig.index(val, i+1)
      except ValueError,e:
        break
      except IndexError,e:
        break
      tmp.append(i)
    indexes.extend(tmp)
  return indexes

def saveIdea(opts, name, results):
  pickle.dump(results, file(name,'w'))
  

def reportCacheValues( cache ):
  log.info('Reporting info on values on stdout')
  # sort by key
  keys = sorted(cache.keys(), reverse=True)
  for k in keys:
    v = cache[k]
    print 'For %d bytes between possible pointers, there is %d PinnedOffsets '%(k, len(v))
    # print nicely top 5
    poffs = sorted(v, reverse=True)
    n = min(5, len(v))
    print '  - the %d longuest sequences are '%(n)
    for poff in poffs[:n]:
      print '\t', poff,
      if len(poff) > 100 :
        print poff.pinned(5)
      else:
        print ''
    print ''

def printStatus(offset1, cache):
  print 'Reading offset %d'%(offset1)
  reportCacheValues(cache)


def saveSequence(value, cacheValues, sig1, offset1, sig2, offset2, match_len ):
  pinned = sig1[offset1:offset1+match_len]
  if value not in cacheValues:
    cacheValues[value] = list()
  #cache it
  cacheValues[value].append( PinnedOffsets( pinned, sig1, offset1, sig2, offset2) )
  return

def savePinned(cacheValues, sig, offset, match_len ):
  pinned = sig.sig[offset:offset+match_len]
  pp = PinnedPointers( pinned, sig, offset)
  s = pp.structLen() 
  if s not in cacheValues:
    cacheValues[s] = list()
  cacheValues[s].append( pp )
  return pp 



def search(opts):
  #
  make(opts)
  pass
  
def argparser():
  rootparser = argparse.ArgumentParser(prog='haystack-pattern', description='Do a discovery structure pattern search.')
  rootparser.add_argument('--debug', action='store_true', help='Debug mode on.')
  #rootparser.add_argument('sigfile', type=argparse.FileType('wb'), action='store', help='The output signature filename.')
  rootparser.add_argument('dumpfiles', type=argparse.FileType('rb'), action='store', help='Source memory dump by haystack.', nargs='*')
  #rootparser.add_argument('dumpfile2', type=argparse.FileType('rb'), action='store', help='Source memory dump by haystack.')
  #rootparser.add_argument('dumpfile3', type=argparse.FileType('rb'), action='store', help='Source memory dump by haystack.')
  rootparser.set_defaults(func=search)  
  return rootparser

def main(argv):
  parser = argparser()
  opts = parser.parse_args(argv)

  level=logging.INFO
  if opts.debug :
    level=logging.DEBUG
  logging.basicConfig(level=level)  
  logging.getLogger('haystack').setLevel(logging.INFO)
  logging.getLogger('dumper').setLevel(logging.INFO)
  logging.getLogger('dumper').setLevel(logging.INFO)

  opts.func(opts)


#def tests():
#  '''
#import pattern 
#pattern.main('../outputs/skype.1.a ../outputs/skype.2.a ../outputs/skype.3.a'.split())
#cacheValues=pattern.cache
#common = pattern.common
#mapper = pattern.mapper
#
#'''
#  pass

if __name__ == '__main__':
  main(sys.argv[1:])