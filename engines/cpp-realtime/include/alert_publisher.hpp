#pragma once
#ifndef ALERT_PUBLISHER_HPP
#define ALERT_PUBLISHER_HPP

#include "redis_client.hpp"
#include "sliding_window.hpp"

#include <string>
#include <chrono>
#include <sstream>
#include <iomanip>
#include <vector>
#include <utility>

class AlertPublisher {
public:
    static constexpr const char* RESULTS_STREAM = "cpp:results";

    explicit AlertPublisher(RedisClient& redis_client)
        : redis_(redis_client) {}

    std::string publish(const AlertResult& alert,
                        const TelemetryEvent& event,
                        const WindowStats& stats);

    static double severity_to_score(const std::string& severity) {
        if (severity == "NONE") return 0.0;
        if (severity == "LOW") return 0.3;
        if (severity == "MEDIUM") return 0.5;
        if (severity == "HIGH") return 0.8;
        if (severity == "CRITICAL") return 1.0;
        return 0.0;
    }

private:
    RedisClient& redis_;

    static std::string fmt_double(double v) {
        std::ostringstream ss;
        ss << std::fixed << std::setprecision(4) << v;
        return ss.str();
    }

    static std::string utc_timestamp() {
        auto now = std::chrono::system_clock::now();
        auto tt  = std::chrono::system_clock::to_time_t(now);
        std::tm* tm_ptr = std::gmtime(&tt);
        if (!tm_ptr) return "1970-01-01T00:00:00Z";
        std::tm tm_buf = *tm_ptr;
        char buf[32];
        std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm_buf);
        return buf;
    }

    static std::string truncate(const std::string& s, size_t max_len) {
        if (s.size() <= max_len) return s;
        return s.substr(0, max_len) + "...";
    }
};

#endif // ALERT_PUBLISHER_HPP
