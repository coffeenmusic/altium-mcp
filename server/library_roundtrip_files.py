"""Strict saved PcbLib STEP evidence; no use of disk data as dirty-buffer data."""

import hashlib
from pathlib import Path
import re
import struct
import zlib

import pythoncom
import pcblib_file as pcb

MAX_MODEL_BYTES = 128*1024*1024


def records(data):
    result=[];offset=0
    while offset<len(data):
        if offset+4>len(data):
            raise ValueError("Truncated model metadata length")
        size=struct.unpack_from('<I',data,offset)[0];offset+=4
        if not size or offset+size>len(data):
            raise ValueError("Invalid model metadata record size")
        result.append(pcb._fields(data[offset:offset+size].rstrip(b'\x00')));offset+=size
    return result


def model_digest(data):
    try:
        decoder=zlib.decompressobj()
        value=decoder.decompress(data,MAX_MODEL_BYTES+1)
        if len(value)>MAX_MODEL_BYTES or decoder.unconsumed_tail:
            raise ValueError("Embedded model exceeds 128 MiB inspection limit")
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Incomplete/ambiguous compressed model payload")
        encoding='zlib'
    except zlib.error:
        value=data;encoding='opaque'
    return dict(sha256=hashlib.sha256(value).hexdigest(),size_bytes=len(value),encoding=encoding)


def stored_text(fields,key):
    utf8=fields.get('%UTF8%'+key)
    return utf8.encode('latin-1').decode('utf-8') if utf8 is not None else fields.get(key,'')


def saved_step_state(path):
    """Used body placements and used model payloads; reports unreadable evidence."""
    if not Path(path).is_file():
        return dict(readable=False,reason="Library has no saved file",footprints={})
    root=None;temp=None;library=None;models=None;storage=None
    try:
        root,temp=pcb._open_root(path)
        payloads={}
        library=root.OpenStorage('Library',None,pcb._CHILD,None,0)
        try:
            models=library.OpenStorage('Models',None,pcb._CHILD,None,0)
            metadata=records(pcb._read_stream(models,'Data'))
            for index,item in enumerate(metadata):
                if item.get('EMBED','').upper()=='TRUE':
                    if item['ID'].upper() in payloads:
                        raise ValueError('Ambiguous embedded model ID')
                    payloads[item['ID'].upper()]=model_digest(pcb._read_stream(models,str(index)))
        except pythoncom.com_error:
            # No Models storage is normal when the library contains no STEP.
            pass
        footprints={}
        for name,kind,*_ in root.EnumElements():
            if kind!=1 or name=='Library':
                continue
            storage=root.OpenStorage(name,None,pcb._CHILD,None,0)
            try:
                # Parameters starts with a binary length; search PATTERN explicitly.
                raw=pcb._read_stream(storage,'Parameters')
                match=pcb._PATTERN.search(raw)
                if not match:
                    continue
                utf8=re.search(rb'%UTF8%PATTERN=([^|\x00]*)',raw)
                pattern=utf8[1].decode('utf-8') if utf8 else match[1].decode('latin-1')
                bodies=[]
                for match in pcb._BODY.finditer(pcb._read_stream(storage,'Data')):
                    fields=pcb._fields(match[0]);embedded=fields.get('MODEL.EMBED','').upper()=='TRUE'
                    model_name=stored_text(fields,'MODEL.NAME');model_id=fields.get('MODELID','').upper()
                    body=dict(model_name=model_name,embedded=embedded,
                              rotation_x=pcb._degrees(fields.get('MODEL.3D.ROTX')),
                              rotation_y=pcb._degrees(fields.get('MODEL.3D.ROTY')),
                              rotation_z=pcb._degrees(fields.get('MODEL.3D.ROTZ')),
                              model_z_offset=pcb._mils(fields.get('MODEL.3D.DZ')))
                    if embedded:
                        body['payload']=payloads.get(model_id)
                    else:
                        asset=Path(model_name)
                        if not asset.is_absolute():
                            asset=Path(path).parent/asset
                        if asset.is_file() and asset.stat().st_size<=MAX_MODEL_BYTES:
                            body['payload']=dict(sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),size_bytes=asset.stat().st_size,encoding='external')
                        else:
                            body['payload']=None
                    bodies.append(body)
                footprints[pattern]=bodies
            except pythoncom.com_error:
                continue
        return dict(readable=True,footprints=footprints)
    except (OSError,ValueError,KeyError,UnicodeError,pythoncom.com_error) as exc:
        return dict(readable=False,reason=str(exc),footprints={})
    finally:
        storage=None;models=None;library=None;root=None
        if temp:
            Path(temp).unlink(missing_ok=True)
