# frozen_string_literal: true

class Alert < ApplicationRecord
  belongs_to :anomaly_score, optional: true

  validates :session_id, presence: true
  validates :severity, presence: true, inclusion: { in: %w[low medium high critical] }
  validates :composite_score, presence: true

  def to_dashboard_json
    {
      id: id,
      session_id: session_id,
      event_id: event_id,
      severity: severity,
      composite_score: composite_score,
      triggered_rules: triggered_rules,
      triggered_at: triggered_at.iso8601,
      acknowledged: acknowledged_at.present?
    }
  end
end
