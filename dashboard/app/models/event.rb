# frozen_string_literal: true

# Event represents a single telemetry event captured by the browser extension.
# Events are the raw data points: navigations, XHR calls, DOM mutations, etc.
class Event < ApplicationRecord
  belongs_to :browser_session, foreign_key: :session_id, primary_key: :session_uuid, optional: true

  validates :event_id, presence: true, uniqueness: true
  validates :session_id, presence: true
  validates :event_type, presence: true
  validates :captured_at, presence: true

  # Valid event types from browser extension
  EVENT_TYPES = %w[
    navigation_start navigation_complete navigation_error
    xhr_request xhr_response fetch_request fetch_response
    dom_mutation tab_created tab_closed tab_activated
    redirect_detected cross_origin_request
  ].freeze

  scope :recent, -> { order(captured_at: :desc) }
  scope :by_session, ->(session_id) { where(session_id: session_id) }
  scope :by_type, ->(type) { where(event_type: type) }
  scope :in_window, ->(start_time, end_time) { where(captured_at: start_time..end_time) }

  # Parse metadata JSON safely
  def metadata_hash
    JSON.parse(metadata || "{}")
  rescue JSON::ParserError
    {}
  end

  def to_dashboard_json
    {
      id: event_id,
      session_id: session_id,
      type: event_type,
      url: url,
      status: response_status,
      time: captured_at.iso8601
    }
  end
end
