# frozen_string_literal: true

class CreateBrowserSessions < ActiveRecord::Migration[7.2]
  def change
    create_table :browser_sessions do |t|
      t.string   :session_uuid,     null: false, index: { unique: true }
      t.string   :user_agent,       null: false, default: "unknown"
      t.float    :max_threat_score, null: false, default: 0.0
      t.integer  :event_count,      null: false, default: 0
      t.string   :ip_address
      t.string   :country_code
      t.datetime :last_seen_at
      t.timestamps
    end

    add_index :browser_sessions, :max_threat_score
    add_index :browser_sessions, :last_seen_at
  end
end
