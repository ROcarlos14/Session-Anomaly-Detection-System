import std/[net, strutils, sequtils, parseutils, tables]

type
  RedisClient* = object
    socket*: Socket
    host*: string
    port*: int
    connected*: bool

  RespValueKind* = enum
    SimpleString, Error, Integer, BulkString, Array, Null

  RespValue* = object
    case kind*: RespValueKind
    of SimpleString, Error, BulkString:
      strVal*: string
    of Integer:
      intVal*: int64
    of Array:
      arrVal*: seq[RespValue]
    of Null:
      discard

  StreamEntry* = object
    id*: string
    fields*: Table[string, string]

proc newRedisClient*(host: string = "127.0.0.1", port: int = 6379): RedisClient =
  result.host = host
  result.port = port
  result.socket = newSocket()
  result.connected = false

proc connect*(client: var RedisClient) =
  if not client.connected:
    try:
      client.socket.connect(client.host, Port(client.port))
      client.connected = true
    except Exception as e:
      client.connected = false
      raise e

proc parseResp*(client: var RedisClient): RespValue =
  var line = ""
  client.socket.readLine(line)
  if line.len == 0:
    raise newException(IOError, "Empty response from Redis")
  
  let prefix = line[0]
  let data = line[1..^1]

  case prefix
  of '+': return RespValue(kind: SimpleString, strVal: data)
  of '-': return RespValue(kind: Error, strVal: data)
  of ':': return RespValue(kind: Integer, intVal: parseInt(data))
  of '$':
    let length = parseInt(data)
    if length == -1: return RespValue(kind: Null)
    var buf = newString(length + 2) # Include \r\n
    let readBytes = client.socket.recv(buf, length + 2)
    return RespValue(kind: BulkString, strVal: buf[0..<length])
  of '*':
    let count = parseInt(data)
    if count == -1: return RespValue(kind: Null)
    var arr = newSeq[RespValue](count)
    for i in 0..<count:
      arr[i] = client.parseResp()
    return RespValue(kind: Array, arrVal: arr)
  else:
    raise newException(ValueError, "Unknown RESP prefix: " & $prefix)

proc sendCommand*(client: var RedisClient, args: seq[string]): RespValue =
  var req = "*" & $args.len & "\r\n"
  for arg in args:
    req.add("$" & $arg.len & "\r\n" & arg & "\r\n")
  
  client.socket.send(req)
  return client.parseResp()

# Simplified commands for prototype
proc xadd*(client: var RedisClient, stream, id: string, fields: seq[(string, string)]): string =
  var args = @["XADD", stream, id]
  for f in fields:
    args.add(f[0])
    args.add(f[1])
  let resp = client.sendCommand(args)
  if resp.kind == BulkString or resp.kind == SimpleString:
    return resp.strVal
  return ""
