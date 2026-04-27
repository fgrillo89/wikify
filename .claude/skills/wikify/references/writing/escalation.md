# Escalation

Default retry policy:

1. First failure: retry once at the same tier with the concrete
   validator error included.
2. Second failure: escalate to tier L.
3. Third failure: mark concept failed.

Do not escalate for missing evidence, unsupported claims, or systematic
prompt/schema bugs. Fix the input or prompt instead.
