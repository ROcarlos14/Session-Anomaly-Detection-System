# frozen_string_literal: true

# AnomalyScore represents the output of the detection engines for a session window.
# Combines ML Engine score and C++ Engine score into a composite threat score.
class AnomalyScore < ApplicationRecord
  belongs_to :browser_session, foreign_key: :session_id, primary_key: :session_uuid, optional: true
  has_many :alerts

  validates :session_id, presence: true
  validates :composite_score, presence: true,
            numericality: { greater_than_or_equal_to: 0.0, less_than_or_equal_to: 1.0 }

  scope :recent, -> { order(scored_at: :desc) }
  scope :high_risk, -> { where("composite_score >= ?", 0.8) }
  scope :by_session, ->(sid) { where(session_id: sid) }

  SEVERITY_THRESHOLDS = {
    "none"     => 0.0..0.4,
    "low"      => 0.4..0.6,
    "medium"   => 0.6..0.8,
    "high"     => 0.8..0.9,
    "critical" => 0.9..1.0
  }.freeze

  def severity
    SEVERITY_THRESHOLDS.find { |_, range| range.include?(composite_score) }&.first || "none"
  end

  def self.average_score_by_session(session_id)
    by_session(session_id).average(:composite_score)&.round(4) || 0.0
  end
end
