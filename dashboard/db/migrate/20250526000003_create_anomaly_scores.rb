# frozen_string_literal: true

class CreateAnomalyScores < ActiveRecord::Migration[7.2]
  def change
    create_table :anomaly_scores do |t|
      t.string   :session_id,      null: false
      t.string   :event_id
      t.float    :composite_score, null: false
      t.float    :ml_score,        default: 0.0
      t.float    :cpp_score,       default: 0.0
      t.float    :if_score,        default: 0.0    # Isolation Forest sub-score
      t.float    :lstm_score,      default: 0.0    # LSTM sub-score
      t.string   :features_json    # Feature vector used for scoring
      t.datetime :scored_at,       null: false
      t.timestamps
    end

    add_index :anomaly_scores, :session_id
    add_index :anomaly_scores, :composite_score
    add_index :anomaly_scores, :scored_at
    add_index :anomaly_scores, [:session_id, :scored_at]
  end
end
