import models, std/[json]

proc aggregate*(ml: MLResult, cpp: CppResult): AggregatedAlert =
  let compositeScore = (0.60 * ml.ml_score) + (0.40 * cpp.cpp_score)
  let severity = scoreToSeverity(compositeScore)
  
  var details = "Combined Anomaly Detected. "
  if cpp.triggered_rules.len > 0:
    details &= "Rules: " & cpp.triggered_rules & ". "
  details &= "ML Score: " & $ml.ml_score & " (IF: " & $ml.if_score & ", LSTM: " & $ml.lstm_score & ")."
  
  result = AggregatedAlert(
    session_id: ml.session_id,
    event_id: ml.event_id,
    composite_score: compositeScore,
    ml_score: ml.ml_score,
    cpp_score: cpp.cpp_score,
    severity: severityToString(severity),
    timestamp_ms: ml.timestamp_ms,
    details: details
  )

proc aggregateMLOnly*(ml: MLResult): AggregatedAlert =
  let compositeScore = ml.ml_score
  let severity = scoreToSeverity(compositeScore)
  result = AggregatedAlert(
    session_id: ml.session_id,
    event_id: ml.event_id,
    composite_score: compositeScore,
    ml_score: ml.ml_score,
    cpp_score: 0.0,
    severity: severityToString(severity),
    timestamp_ms: ml.timestamp_ms,
    details: "ML-only Anomaly Detected. Score: " & $ml.ml_score
  )

proc aggregateCppOnly*(cpp: CppResult): AggregatedAlert =
  let compositeScore = cpp.cpp_score
  let severity = scoreToSeverity(compositeScore)
  result = AggregatedAlert(
    session_id: cpp.session_id,
    event_id: cpp.event_id,
    composite_score: compositeScore,
    ml_score: 0.0,
    cpp_score: cpp.cpp_score,
    severity: severityToString(severity),
    timestamp_ms: cpp.timestamp_ms,
    details: "Rule-based Anomaly Detected. Rules: " & cpp.triggered_rules
  )

proc buildAlertJson*(alert: AggregatedAlert): string =
  let j = %*{
    "session_id": alert.session_id,
    "event_id": alert.event_id,
    "composite_score": alert.composite_score,
    "ml_score": alert.ml_score,
    "cpp_score": alert.cpp_score,
    "severity": alert.severity,
    "triggered_rules": alert.details,
    "timestamp_ms": alert.timestamp_ms
  }
  return $j

proc shouldAlert*(alert: AggregatedAlert, threshold: float = 0.40): bool =
  return alert.composite_score >= threshold
