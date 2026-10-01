# frozen_string_literal: true

class CreateUsers < ActiveRecord::Migration[7.2]
  def change
    create_table :users do |t|
      t.string   :name,            null: false
      t.string   :email,           null: false
      t.string   :password_digest, null: false
      t.string   :role,            null: false, default: "analyst"
      t.boolean  :active,          null: false, default: true
      t.datetime :last_login_at
      t.timestamps
    end

    add_index :users, :email, unique: true
    add_index :users, :role
  end
end
