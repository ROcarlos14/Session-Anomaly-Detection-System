# frozen_string_literal: true

module Api
  module V1
    class AlertsController < ApplicationController
      skip_before_action :verify_authenticity_token

      def index
        alerts = Alert.order(triggered_at: :desc).limit(100)
        render json: alerts
      end

      def show
        alert = Alert.find(params[:id])
        render json: alert
      end

      def acknowledge
        alert = Alert.find(params[:id])
        alert.update!(
          acknowledged_at: Time.current,
          acknowledged_by: params[:user_id] # In a real app, current_user.id
        )
        
        # Broadcast the acknowledgment so all dashboards update
        ActionCable.server.broadcast("alerts_channel", { type: "alert_acknowledged", id: alert.id })
        
        render json: { status: "acknowledged", id: alert.id }
      end
    end
  end
end
