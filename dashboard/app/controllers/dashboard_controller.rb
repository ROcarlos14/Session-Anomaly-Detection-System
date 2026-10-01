# frozen_string_literal: true

class DashboardController < ApplicationController
  layout false

  def index
    # Fetch initial state for SOC Dashboard
    @stats = fetch_stats
    @recent_alerts = Alert.order(triggered_at: :desc).limit(100)
    
    # Detailed sessions for the Sessions table
    @active_sessions = BrowserSession.order(last_seen_at: :desc).limit(50)
    
    # Raw telemetry events for the Event Stream
    @recent_events = Event.order(captured_at: :desc).limit(100)
    
    # 60 minute timeline
    @threat_timeline = generate_timeline
  end

  def stats
    render json: fetch_stats
  end

  private

  def fetch_stats
    {
      critical_alerts: Alert.where(severity: "critical").where("triggered_at > ?", 24.hours.ago).count,
      unacknowledged_alerts: Alert.where(acknowledged_at: nil).count,
      active_sessions: BrowserSession.where("last_seen_at > ?", 5.minutes.ago).count,
      events_per_minute: Event.where("captured_at > ?", 1.minute.ago).count,
      avg_threat_score: BrowserSession.average(:max_threat_score) || 0.0,
      total_events_today: Event.where("captured_at > ?", Time.current.beginning_of_day).count
    }
  end

  def generate_timeline
    # Return array of { time: "HH:MM", score: float }
    # Simplified mock for the timeline until enough data exists
    now = Time.current
    (0..60).map do |i|
      time = now - (60 - i).minutes
      {
        time: time.strftime("%H:%M"),
        score: rand(0.0..0.3) # baseline noise
      }
    end
  end
end
