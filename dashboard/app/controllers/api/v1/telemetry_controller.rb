# frozen_string_literal: true

module Api
  module V1
    class TelemetryController < ApplicationController
      # Disable CSRF for API endpoints
      skip_before_action :verify_authenticity_token

      def create
        # Use TelemetryIngestionService to handle payload
        service_result = TelemetryIngestionService.new(
          session_id: params[:session_id],
          events:     params[:events],
          user_agent: request.user_agent || params[:user_agent],
          ip_address: request.remote_ip
        ).call

        if service_result[:success]
          render json: { status: "ok", ingested: service_result[:event_count] }, status: :accepted
        else
          render json: { status: "error", message: service_result[:error] }, status: :unprocessable_entity
        end
      end
    end
  end
end
