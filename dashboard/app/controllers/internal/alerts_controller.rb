# frozen_string_literal: true

module Internal
  class AlertsController < ApplicationController
    skip_before_action :verify_authenticity_token

    def create
      # The Nim orchestrator POSTs aggregated alerts here
      # Payload: { session_id, event_id, composite_score, ml_score, cpp_score, severity, triggered_rules, timestamp_ms }
      
      session_id = params[:session_id]
      score = params[:composite_score].to_f

      ActiveRecord::Base.transaction do
        # 1. Update session threat score
        session = BrowserSession.find_by(session_uuid: session_id)
        if session
          session.update!(max_threat_score: [session.max_threat_score, score].max)
        end

        # 2. Record AnomalyScore
        anomaly = AnomalyScore.create!(
          session_id: session_id,
          event_id: params[:event_id],
          composite_score: score,
          ml_score: params[:ml_score].to_f,
          cpp_score: params[:cpp_score].to_f,
          scored_at: Time.current
        )

        # 3. Create Alert
        alert = Alert.create!(
          session_id: session_id,
          event_id: params[:event_id],
          anomaly_score: anomaly,
          severity: params[:severity] || "low",
          composite_score: score,
          ml_score: params[:ml_score].to_f,
          cpp_score: params[:cpp_score].to_f,
          triggered_rules: params[:triggered_rules],
          triggered_at: Time.current
        )

        # 4. Broadcast via ActionCable to SOC Dashboard
        ActionCable.server.broadcast("alerts_channel", alert.to_dashboard_json)
      end

      render json: { status: "created" }, status: :created
    rescue StandardError => e
      Rails.logger.error "[Internal::AlertsController] Error: #{e.message}"
      render json: { error: e.message }, status: :unprocessable_entity
    end
  end
end
