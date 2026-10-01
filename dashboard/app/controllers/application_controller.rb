# frozen_string_literal: true

# ApplicationController base with JWT authentication helpers
class ApplicationController < ActionController::Base
  include ActionController::Cookies

  # Protect from CSRF for non-API endpoints
  protect_from_forgery with: :exception, unless: :api_request?

  helper_method :current_user, :logged_in?

  private

  def current_user
    @current_user ||= begin
      if session[:user_id]
        User.find_by(id: session[:user_id])
      elsif request.headers["Authorization"]
        decode_jwt_user
      end
    end
  end

  def logged_in?
    current_user.present?
  end

  def require_login!
    unless logged_in?
      respond_to do |format|
        format.html { redirect_to root_path, alert: "Please log in." }
        format.json { render json: { error: "Unauthorized" }, status: :unauthorized }
      end
    end
  end

  def require_admin!
    require_login!
    unless current_user&.admin?
      render json: { error: "Forbidden" }, status: :forbidden
    end
  end

  def decode_jwt_user
    token = request.headers["Authorization"].sub(/\ABearer\s/, "")
    payload = JWT.decode(token, Rails.application.secret_key_base, true, { algorithm: "HS256" })[0]
    User.find_by(id: payload["user_id"])
  rescue JWT::DecodeError, JWT::ExpiredSignature
    nil
  end

  def api_request?
    request.path.start_with?("/api/") || request.path.start_with?("/internal/")
  end
end
