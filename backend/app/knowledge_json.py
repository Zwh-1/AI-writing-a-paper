"""Syntax-only structured output decoding; never invents missing fields/values."""
import ast
import json
import math
import re


def escape_reason_quotes(value):
    """Escape only interior quotes in a bounded no_claim_reason string.

    The next entities/claims field must be explicit. Other fields, missing
    delimiters and incomplete responses are never reconstructed here.
    """
    pattern=r'("no_claim_reason"\s*:\s*")([^{}\[\]]*?)("(?=\s*,\s*"(?:entities|claims)"\s*:))'
    def replace(match):
        body=match.group(2)
        if re.search(r'"\s*:',body):return match.group(0)
        escaped=[];slashes=0
        for char in body:
            if char=='"' and slashes%2==0:escaped.append('\\')
            escaped.append(char)
            slashes=slashes+1 if char=='\\' else 0
        return match.group(1)+''.join(escaped)+match.group(3)
    return re.sub(pattern,replace,value)


def parse(text):
    value=text.strip();fence=re.fullmatch(r'```(?:json|python)?\s*(.*?)\s*```',value,re.S|re.I)
    if fence:value=fence.group(1)
    repaired=False
    try:data=json.loads(value)
    except json.JSONDecodeError:
        try:data=ast.literal_eval(value)
        except (ValueError,SyntaxError):
            escaped=escape_reason_quotes(value)
            if escaped==value:raise
            data=json.loads(escaped)
        repaired=True
    def check(obj):
        if obj is None or isinstance(obj,(str,bool,int)):return
        if isinstance(obj,float) and math.isfinite(obj):return
        if isinstance(obj,list):
            for item in obj:check(item)
            return
        if isinstance(obj,dict) and all(isinstance(k,str) for k in obj):
            for item in obj.values():check(item)
            return
        raise ValueError('Structured output contains non-JSON values')
    check(data)
    return data,json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False),repaired
