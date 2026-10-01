# frozen_string_literal: true

# AlertsChannel is the ActionCable WebSocket channel for real-time alert delivery.
# SOC analysts subscribe to this channel to receive live threat alerts without polling.
class AlertsChannel < ApplicationCable::Channel
  def subscribed
    stream_from "alerts_channel"
    Rails.logger.info "[ActionCable] SOC client subscribed to alerts_channel"
  end

  def unsubscribed
    stop_all_streams
    Rails.logger.info "[ActionCable] SOC client unsubscribed from alerts_channel"
  end

  # SOC analyst can acknowledge an alert via WebSocket
  def acknowledge(data)
    alert_id = data["alert_id"]
    alert = Alert.find_by(id: alert_id)

    if alert
      alert.acknowledge!(user_id: data["user_id"])
      ActionCable.server.broadcast("alerts_channel", {
        type:       "alert_acknowledged",
        alert_id:   alert_id,
        ack_by:     data["user_id"],
        ack_at:     Time.current.iso8601
      })
    end
  end

  # SOC analyst can update alert thresholds via WebSocket
  def update_threshold(data)
    return unless current_user&.admin?

    Rails.cache.write("alert_threshold_#{data['severity']}", data["threshold"].to_f)
    ActionCable.server.broadcast("alerts_channel", {
      type:      "threshold_updated",
      severity:  data["severity"],
      threshold: data["threshold"]
    })
  end
end
