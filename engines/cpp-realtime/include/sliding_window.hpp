#pragma once
#include <string>
#include <vector>
#include <map>
#include <iostream>

struct TelemetryEvent {
    std::string session_id;
    std::string event_id;
    std::string event_type;
    std::string url;
    long long timestamp_ms;
    std::string metadata;
    bool is_cross_origin;
    int dom_mutation_count;
};

struct WindowStats {
    double requests_per_second;
    int unique_domains;
    int redirect_chain_depth;
    double xhr_error_rate;
    double dom_mutation_rate;
    double cross_origin_ratio;
    double tab_switch_rate;
    double burst_score;
};

template <typename T>
class SlidingWindow {
public:
    SlidingWindow(long long time_horizon_ms = 60000) : horizon_ms(time_horizon_ms) {}

    void add_event(const T& event) {
        long long current_time = event.timestamp_ms;
        events.push_back(event);
        
        // Evict old events
        auto it = events.begin();
        while (it != events.end() && (current_time - it->timestamp_ms) > horizon_ms) {
            it = events.erase(it);
        }
    }

    WindowStats get_window_stats() const {
        WindowStats stats = {0};
        if (events.empty()) return stats;

        long long duration = events.back().timestamp_ms - events.front().timestamp_ms;
        if (duration == 0) duration = 1000; // avoid div by 0

        double seconds = duration / 1000.0;
        stats.requests_per_second = events.size() / seconds;
        
        int cross_origin_count = 0;
        int dom_mutations = 0;

        for (const auto& ev : events) {
            if (ev.is_cross_origin) cross_origin_count++;
            dom_mutations += ev.dom_mutation_count;
        }

        stats.cross_origin_ratio = static_cast<double>(cross_origin_count) / events.size();
        stats.dom_mutation_rate = dom_mutations / seconds;
        
        return stats;
    }

    bool empty() const { return events.empty(); }

private:
    long long horizon_ms;
    std::vector<T> events;
};

struct AlertResult {
    bool is_alert;
    std::string severity;
    std::string triggered_rules;
    double score;
};

class ThresholdChecker {
public:
    AlertResult check(const WindowStats& stats) {
        AlertResult result = {false, "NONE", "", 0.0};
        
        if (stats.requests_per_second > 50) {
            result.is_alert = true;
            result.severity = "HIGH";
            result.triggered_rules += "High RPS;";
            result.score = std::max(result.score, 0.8);
        }
        if (stats.dom_mutation_rate > 50) {
            result.is_alert = true;
            result.severity = "HIGH";
            result.triggered_rules += "Extreme DOM Mutation;";
            result.score = std::max(result.score, 0.8);
        }
        if (stats.cross_origin_ratio > 0.80) {
            result.is_alert = true;
            if (result.severity != "HIGH") result.severity = "MEDIUM";
            result.triggered_rules += "High Cross-Origin Ratio;";
            result.score = std::max(result.score, 0.5);
        }

        return result;
    }
};
