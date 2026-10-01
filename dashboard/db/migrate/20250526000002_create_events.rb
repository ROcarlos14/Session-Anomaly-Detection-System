# frozen_string_literal: true

class CreateEvents < ActiveRecord::Migration[7.2]
  def change
    create_table :events do |t|
      t.string   :event_id,           null: false, index: { unique: true }
      t.string   :session_id,         null: false
      t.string   :event_type,         null: false
      t.text     :url
      t.datetime :captured_at,        null: false
      t.integer  :tab_id
      t.boolean  :is_cross_origin,    default: false
      t.integer  :response_status
      t.bigint   :response_size_bytes
      t.integer  :dom_mutation_count, default: 0
      t.integer  :redirect_count,     default: 0
      t.text     :metadata
      t.timestamps
    end

    add_index :events, :session_id
    add_index :events, :event_type
    add_index :events, :captured_at
    add_index :events, [:session_id, :captured_at]
  end
end
