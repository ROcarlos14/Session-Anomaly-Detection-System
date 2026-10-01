require 'redis'
require 'net/http'
require 'json'

redis = Redis.new(url: ENV.fetch("REDIS_URL", "redis://localhost:6379/0"))
dashboard_url = URI("http://localhost:3000/internal/alerts")

puts "Ruby Orchestrator starting..."

last_id = "0-0"

loop do
  begin
    messages = redis.xread(["ml:results"], [last_id], count: 10, block: 1000)
    next unless messages && !messages.empty?

    messages["ml:results"].each do |msg_id, fields|
      puts "Processing message #{msg_id}"
      last_id = msg_id
      
      # Send to dashboard
      payload = {
        session_id: fields["session_id"],
        event_id: fields["event_id"],
        composite_score: fields["ml_score"], # fallback if no cpp
        ml_score: fields["ml_score"],
        cpp_score: fields["cpp_score"] || 0.0,
        severity: fields["ml_score"].to_f >= 0.8 ? "critical" : (fields["ml_score"].to_f >= 0.6 ? "high" : "medium"),
        triggered_rules: "ML Anomaly Detected",
        timestamp_ms: Time.now.to_i * 1000
      }

      begin
        http = Net::HTTP.new(dashboard_url.host, dashboard_url.port)
        request = Net::HTTP::Post.new(dashboard_url)
        request['Content-Type'] = 'application/json'
        request.body = payload.to_json
        response = http.request(request)
        puts "Posted to dashboard: #{response.code}"
      rescue => e
        puts "Failed to post to dashboard: #{e.message}"
      end
    end
  rescue => e
    puts "Error: #{e.message}"
    sleep 1
  end
end
