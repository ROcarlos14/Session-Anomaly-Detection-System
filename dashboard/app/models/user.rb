# frozen_string_literal: true

# User represents a SOC analyst who can log into the dashboard.
class User < ApplicationRecord
  has_secure_password

  validates :email, presence: true, uniqueness: { case_sensitive: false },
            format: { with: URI::MailTo::EMAIL_REGEXP }
  validates :name, presence: true
  validates :role, inclusion: { in: %w[analyst admin viewer] }

  before_save { self.email = email.downcase }

  scope :active, -> { where(active: true) }
  scope :admins, -> { where(role: "admin") }

  # Generate JWT token for API auth (browser extension + API consumers)
  def generate_jwt
    payload = {
      user_id:  id,
      email:    email,
      role:     role,
      exp:      24.hours.from_now.to_i
    }
    JWT.encode(payload, Rails.application.secret_key_base, "HS256")
  end

  def admin?
    role == "admin"
  end

  def analyst?
    role == "analyst"
  end
end
