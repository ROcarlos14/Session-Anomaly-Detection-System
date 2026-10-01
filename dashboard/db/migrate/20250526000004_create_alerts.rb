# frozen_string_literal: true

class CreateAlerts < ActiveRecord::Migration[7.2]
  def change
    create_table :alerts do |t|
      t.string   :session_id,      null: false
      t.string   :event_id
      t.references :anomaly_score,  foreign_key: true
      t.string   :severity,        null: false   # low, medium, high, critical
      t.float    :composite_score, null: false
      t.float    :ml_score,        default: 0.0
      t.float    :cpp_score,       default: 0.0
      t.text     :triggered_rules               # JSON array of rule names
      t.text     :details                        # Human-readable description
      t.datetime :triggered_at,    null: false
      t.datetime :acknowledged_at
      t.integer  :acknowledged_by               # user ID
      t.timestamps
    end

    add_index :alerts, :session_id
    add_index :alerts, :severity
    add_index :alerts, :triggered_at
    add_index :alerts, :acknowledged_at
    add_index :alerts, [:severity, :acknowledged_at]
  end
end
