FROM ruby:3.2.3-slim

RUN apt-get update -qq && apt-get install -y \
    build-essential \
    libpq-dev \
    tzdata \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Gemfile Gemfile.lock* ./
RUN bundle config set --local without 'development test'
# Fix for windows lock file since we copied from windows
RUN rm -f Gemfile.lock
RUN bundle install --jobs 4 --retry 3

COPY . .

# Puma is already in the Gemfile

ENV RAILS_ENV=development
ENV RACK_ENV=development

EXPOSE 3000

CMD ["sh", "-c", "rm -f tmp/pids/server.pid && bundle exec rails db:create db:migrate && bundle exec puma -C config/puma.rb"]
