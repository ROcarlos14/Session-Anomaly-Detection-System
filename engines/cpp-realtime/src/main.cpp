#include "redis_client.hpp"
#include "sliding_window.hpp"
#include "alert_publisher.hpp"
#include <iostream>
#include <unistd.h>

int main() {
    std::cout << "Starting C++ Anomaly Engine..." << std::endl;
    
    RedisClient redis;
    // Use the Docker service name 'redis' instead of localhost
    if (!redis.connect_redis("redis", 6379)) {
        std::cerr << "Failed to connect to Redis" << std::endl;
        return 1;
    }
    
    std::cout << "Connected to Redis. Listening for telemetry..." << std::endl;
    
    // Create group (ignoring if it exists)
    redis.execute_simple("*6\r\n$5\r\nXINFO\r\n$6\r\nGROUPS\r\n$13\r\nraw:telemetry\r\n");
    redis.execute_simple("*7\r\n$6\r\nXGROUP\r\n$6\r\nCREATE\r\n$13\r\nraw:telemetry\r\n$10\r\ncpp-engine\r\n$1\r\n$\r\n$8\r\nMKSTREAM\r\n");

    AlertPublisher publisher(redis);
    SlidingWindow<TelemetryEvent> window(60000); // Global window for simplicity in prototype
    ThresholdChecker checker;
    
    while (true) {
        // XREADGROUP GROUP cpp-engine cpp-worker-1 COUNT 10 BLOCK 5000 STREAMS raw:telemetry >
        std::string cmd = "*13\r\n$10\r\nXREADGROUP\r\n$5\r\nGROUP\r\n$10\r\ncpp-engine\r\n$12\r\ncpp-worker-1\r\n$5\r\nCOUNT\r\n$2\r\n10\r\n$5\r\nBLOCK\r\n$4\r\n5000\r\n$7\r\nSTREAMS\r\n$13\r\nraw:telemetry\r\n$1\r\n>\r\n";
        std::string reply = redis.execute_simple(cmd);
        
        // Very naive check to see if we got data (a real implementation would parse RESP properly)
        if (reply.find("session_id") != std::string::npos) {
            std::cout << "Received telemetry event! Processing..." << std::endl;
            
            // Dummy event
            TelemetryEvent ev;
            ev.session_id = "test-session";
            ev.event_id = "test-event";
            ev.event_type = "click";
            ev.timestamp_ms = 123456789;
            ev.dom_mutation_count = 60; // Force an alert!
            ev.is_cross_origin = false;
            
            window.add_event(ev);
            WindowStats stats = window.get_window_stats();
            AlertResult alert = checker.check(stats);
            
            if (alert.is_alert) {
                std::cout << "Alert triggered! Severity: " << alert.severity << std::endl;
                publisher.publish(alert, ev, stats);
            }
        }
    }
    
    return 0;
}
