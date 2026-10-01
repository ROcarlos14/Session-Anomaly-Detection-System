# frozen_string_literal: true

# BrowserSession represents a single user browser session tracked by the extension.
# A session begins when the extension starts and ends when the browser closes.
class BrowserSession < ApplicationRecord
  has_many :events, foreign_key: :session_id, primary_key: :session_uuid
  has_many :anomaly_scores, foreign_key: :session_id, primary_key: :session_uuid
  has_many :alerts, foreign_key: :session_id, primary_key: :session_uuid

  validates :session_uuid, presence: true, uniqueness: true
  validates :user_agent, presence: true

  scope :active, -> { where("last_seen_at > ?", 30.minutes.ago) }
  scope :anomalous, -> { where("max_threat_score >= ?", 0.6) }
  scope :recent, -> { order(created_at: :desc) }

  # Severity level based on max threat score
  def severity
    case max_threat_score
    when 0.0...0.4  then "none"
    when 0.4...0.6  then "low"
    when 0.6...0.8  then "medium"
    when 0.8...0.9  then "high"
    else                 "critical"
    end
  end

  def active?
    last_seen_at.present? && last_seen_at > 5.minutes.ago
  end

  def to_dashboard_json
    {
      id: session_uuid,
      score: max_threat_score.to_f,
      active: active?,
      os: parse_os_from_ua,
      events: event_count,
      last_seen: last_seen_at&.iso8601,
      ip_address: ip_address || "N/A"
    }
  end

  private

  def parse_os_from_ua
    return "Unknown" if user_agent.blank?
    return "Windows" if user_agent.include?("Windows")
    return "macOS" if user_agent.include?("Mac OS")
    return "Linux" if user_agent.include?("Linux")
    "Ruby Scripts" # For our ruby simulator
  end
end
