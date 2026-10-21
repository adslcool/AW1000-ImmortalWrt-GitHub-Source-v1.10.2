#!/usr/bin/env python3
"""Extract RevCPatch from the ARM32 relocatable modules in the cited AW1000 dump.
Requires pyelftools. Usage: python3 extract_patch.py slic.ko output.bin
"""
import hashlib,json,struct,sys
from pathlib import Path
from elftools.elf.elffile import ELFFile

def extract(path):
 with open(path,'rb') as f:
  elf=ELFFile(f)
  assert elf.elfclass==32 and elf.little_endian and elf['e_type']=='ET_REL'
  syms=elf.get_section_by_name('.symtab')
  sym=syms.get_symbol_by_name('RevCPatch')[0]
  sec=elf.get_section(sym['st_shndx']); b=sec.data(); off=sym['st_value']
  assert sym['st_size']==20
  data_ptr,entries_ptr,serial,addr_ptr,values_ptr=struct.unpack_from('<5I',b,off)
  assert serial==0x05262017
  # All four pointers must be ABS32 relocations into this same section.
  rel=next(s for s in elf.iter_sections() if s['sh_type']=='SHT_REL' and s['sh_info']==sym['st_shndx'])
  reloc={r['r_offset']:r for r in rel.iter_relocations()}
  for field in [0,4,12,16]:
   r=reloc[off+field];target=syms.get_symbol(r['r_info_sym'])
   assert r['r_info_type']==2 and target['st_shndx']==sym['st_shndx'] and target['st_value']==0
  data=[]
  for i in range(1024):
   v=struct.unpack_from('<I',b,data_ptr+4*i)[0];data.append(v)
   if v==0:break
  assert data[-1]==0
  entries=list(struct.unpack_from('<16H',b,entries_ptr))
  addresses=[]
  for i in range(65):
   a=struct.unpack_from('<H',b,addr_ptr+2*i)[0]
   if a==0:break
   addresses.append(a)
  assert len(addresses)<=64 and a==0
  values=list(struct.unpack_from('<%dI'%len(addresses),b,values_ptr))
  out=b'FXSP'+struct.pack('<II',serial,len(data))+struct.pack('<%dI'%len(data),*data)+struct.pack('<16H',*entries)+struct.pack('<I',len(addresses))+struct.pack('<%dH'%len(addresses),*addresses)+struct.pack('<%dI'%len(values),*values)
  meta=dict(source_module=str(path),source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),patch_id=hex(serial),n_data=len(data),nonzero_program_words=len(data)-1,entries=entries,support_addresses=addresses,support_values=values,patch_sha256=hashlib.sha256(out).hexdigest(),size=len(out),rodata_file_offset=sec['sh_offset'],structure_offset=off,pointers=[data_ptr,entries_ptr,addr_ptr,values_ptr])
  return out,meta
if __name__=='__main__':
 out,meta=extract(sys.argv[1]);Path(sys.argv[2]).write_bytes(out);print(json.dumps(meta,indent=2))
