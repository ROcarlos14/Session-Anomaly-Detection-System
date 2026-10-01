import std/[json, tables]

type
  SeverityLevel* = enum
    None, Low, Medium, High, Critical

  MLResult* = object
    session_id*: string
    event_id*: string
    ml_score*: float
    if_score*: float
    lstm_score*: float
    timestamp_ms*: int64

  CppResult* = object
    session_id*: string
    event_id*: string
    cpp_score*: float
    severity*: string
    triggered_rules*: string
    timestamp_ms*: int64

  AggregatedAlert* = object
    session_id*: string
    event_id*: string
    composite_score*: float
    ml_score*: float
    cpp_score*: float
    severity*: string
    timestamp_ms*: int64
    details*: string

proc scoreToSeverity*(score: float): SeverityLevel =
  if score >= 0.90: return Critical
  if score >= 0.80: return High
  if score >= 0.60: return Medium
  if score >= 0.40: return Low
  return None

proc severityToString*(s: SeverityLevel): string =
  case s:
    of Critical: "critical"
    of High: "high"
    of Medium: "medium"
    of Low: "low"
    of None: "none"
