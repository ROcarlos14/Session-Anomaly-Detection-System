# frozen_string_literal: true

Rails.application.routes.draw do
  # Dashboard UI
  root "dashboard#index"

  # API Endpoints
  namespace :api do
    namespace :v1 do
      # Telemetry ingestion from browser extension
      post "/telemetry", to: "telemetry#create"
      
      # Dashboard stats
      get "/stats", to: "dashboard#stats"

      # Alert management
      resources :alerts, only: [:index, :show] do
        member do
          patch :acknowledge
        end
      end
    end
  end

  # Internal API for Nim Orchestrator to push alerts
  namespace :internal do
    post "/alerts", to: "alerts#create"
  end

  # ActionCable WebSocket
  mount ActionCable.server => "/cable"
end
