fix(cut,export): normalize plan segment fields before use

Plan segments were read raw: title: null crashed the cut task on .strip(),
an int index made safe_basename raise TypeError, and the JianYing exporter
looked up str-keyed source/offset maps with the int index, falling back to
prefix matching and losing split offsets. Both readers now pass each
segment through a shared normalize_plan_segment() that coerces the text
fields to str.

Refs: B-06
