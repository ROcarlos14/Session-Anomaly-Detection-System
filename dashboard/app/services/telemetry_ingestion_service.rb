# frozen_string_literal: true

# TelemetryIngestionService processes incoming telemetry batches from the browser extension.
# Responsibilities:
#   1. Find or create the BrowserSession
#   2. Persist each Event to PostgreSQL
#   3. Publish the batch to Redis Stream `raw:telemetry` for engine processing
class TelemetryIngestionService
  STREAM_NAME = "raw:telemetry"
  MAX_STREAM_LENGTH = 100_000

  def initialize(params)
    @params     = params
    @session_id = params[:session_id]
    @events     = params[:events] || []
    @user_agent = params[:user_agent] || "unknown"
    @ip_address = params[:ip_address]
  end

  def call
    return { success: false, error: "session_id is required" } if @session_id.blank?
    return { success: false, error: "events array is required" } if @events.empty?

    ActiveRecord::Base.transaction do
      session = find_or_create_session
      persist_events(session)
      publish_to_redis
    end

    { success: true, event_count: @events.size, session_id: @session_id }
  rescue Redis::BaseError => e
    Rails.logger.error "[TelemetryIngestion] Redis error: #{e.message}"
    # Fail gracefully — events are already persisted to PG, Redis publish failed
    { success: true, event_count: @events.size, session_id: @session_id, redis_warning: e.message }
  rescue StandardError => e
    Rails.logger.error "[TelemetryIngestion] Error: #{e.message}"
    { success: false, error: e.message }
  end

  private

  def find_or_create_session
    BrowserSession.find_or_create_by(session_uuid: @session_id) do |s|
      s.user_agent       = @user_agent
      s.ip_address       = @ip_address
      s.max_threat_score = 0.0
      s.last_seen_at     = Time.current
    end.tap do |s|
      s.update!(last_seen_at: Time.current, ip_address: @ip_address) if s.persisted?
    end
  end

  def persist_events(session)
    timestamp = Time.current
    event_records = @events.map do |event_data|
      {
        event_id:           event_data[:event_id],
        session_id:         @session_id,
        event_type:         event_data[:type] || event_data[:event_type],
        url:                event_data[:url]&.truncate(2000),
        captured_at:        parse_timestamp(event_data[:timestamp]),
        tab_id:             event_data[:tab_id],
        is_cross_origin:    event_data[:is_cross_origin],
        response_status:    event_data[:response_status],
        dom_mutation_count: event_data[:dom_mutation_count],
        metadata:           event_data[:metadata]&.to_json,
        created_at:         timestamp,
        updated_at:         timestamp
      }
    end
    Event.insert_all(event_records, unique_by: :event_id) if event_records.any?
  end

  def publish_to_redis
    redis = Redis.new(url: ENV.fetch("REDIS_URL", "redis://localhost:6379/0"))

    payload = {
      session_id:        @session_id,
      user_agent:        @user_agent,
      events:            @events.to_json,
      event_count:       @events.size,
      ingested_at:       Time.current.to_i * 1000  # Unix ms
    }

    redis.xadd(STREAM_NAME, payload, maxlen: MAX_STREAM_LENGTH, approximate: true)
    redis.close
  end

  def parse_timestamp(ts)
    return Time.current if ts.blank?

    if ts.is_a?(Numeric)
      Time.at(ts / 1000.0)
    else
      Time.parse(ts.to_s)
    end
  rescue ArgumentError
    Time.current
  end
end
