import models, redis_client, aggregator
import std/[os, times, strutils, httpclient, json, tables]

proc main() =
  echo "Starting Nim Orchestrator..."
  let redisHost = getEnv("REDIS_HOST", "127.0.0.1")
  let redisPort = parseInt(getEnv("REDIS_PORT", "6379"))
  let dashboardUrl = getEnv("DASHBOARD_URL", "http://localhost:3000")
  
  var redis = newRedisClient(redisHost, redisPort)
  
  var connected = false
  for i in 1..5:
    try:
      redis.connect()
      echo "Connected to Redis successfully."
      connected = true
      break
    except:
      echo "Failed to connect to Redis. Retrying in 2 seconds..."
      sleep(2000)
      
  if not connected:
    echo "Could not connect to Redis. Exiting."
    quit(1)

  # Initialize HttpClient to push alerts to Rails Dashboard
  let railsClient = newHttpClient()
  railsClient.headers = newHttpHeaders({ "Content-Type": "application/json" })

  echo "Orchestrator ready. Listening for ml:results and cpp:results streams..."

  # In a production environment, this would use XREADGROUP to wait for messages
  # For the prototype, we simulate the polling loop
  while true:
    # Sleep to prevent burning CPU
    sleep(1000)
    
    # 1. Read from ml:results and cpp:results
    # 2. Fuse scores using aggregator.nim
    # 3. If an alert is generated, POST to Rails API
    
    # Example snippet of how we'd post to rails if we had an alert:
    # let alertJson = buildAlertJson(alert)
    # try:
    #   discard railsClient.post("http://localhost:3000/internal/alerts", body = alertJson)
    # except:
    #   echo "Failed to send alert to Dashboard"

when isMainModule:
  main()
