#include "../include/alert_publisher.hpp"

#include <iostream>
#include <sstream>
#include <iomanip>
#include <stdexcept>
#include <algorithm>

std::string AlertPublisher::publish(const AlertResult& alert,
                                    const TelemetryEvent& event,
                                    const WindowStats& stats) {
    double cpp_score = severity_to_score(alert.severity);

    std::vector<std::pair<std::string, std::string>> fields;
    fields.reserve(16);

    fields.push_back({"session_id",  event.session_id});
    fields.push_back({"event_id",    event.event_id});
    fields.push_back({"event_type",  event.event_type});
    fields.push_back({"url",         truncate(event.url, 500)});

    fields.push_back({"cpp_score",       fmt_double(cpp_score)});
    fields.push_back({"raw_score",       fmt_double(alert.score)});
    fields.push_back({"severity",        alert.severity});
    fields.push_back({"triggered_rules", alert.triggered_rules});

    fields.push_back({"timestamp",    utc_timestamp()});
    fields.push_back({"timestamp_ms", std::to_string(event.timestamp_ms)});

    fields.push_back({"window_rps",
                      fmt_double(stats.requests_per_second)});
    fields.push_back({"window_unique_domains",
                      std::to_string(stats.unique_domains)});
    fields.push_back({"window_redirect_depth",
                      std::to_string(stats.redirect_chain_depth)});
    fields.push_back({"window_xhr_error_rate",
                      fmt_double(stats.xhr_error_rate)});
    fields.push_back({"window_dom_mutation_rate",
                      fmt_double(stats.dom_mutation_rate)});
    fields.push_back({"window_cross_origin_ratio",
                      fmt_double(stats.cross_origin_ratio)});

    try {
        std::string entry_id = redis_.xadd(RESULTS_STREAM, "*", fields);

        std::cout << "[AlertPublisher] Published to " << RESULTS_STREAM
                  << " id=" << entry_id
                  << " session=" << event.session_id
                  << " severity=" << alert.severity
                  << " score=" << fmt_double(cpp_score)
                  << " rules=" << alert.triggered_rules
                  << std::endl;

        return entry_id;

    } catch (const std::exception& e) {
        std::cerr << "[AlertPublisher] Failed to publish alert: " << e.what()
                  << " session=" << event.session_id << std::endl;
        return "";
    }
}
