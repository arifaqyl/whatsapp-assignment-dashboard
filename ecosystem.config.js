module.exports = {
  apps: [
    {
      name: "student-bot",
      script: "bot.py",
      cwd: "/root/student-bot",
      interpreter: "python3"
    },
    {
      name: "webhook-receiver",
      script: "webhook_receiver.py",
      cwd: "/root/student-bot",
      interpreter: "python3"
    },
    {
      name: "daily-digest",
      script: "daily_digest_worker.py",
      cwd: "/root/student-bot",
      interpreter: "python3"
    },
    {
      name: "ops-console",
      script: "run_ops_console.py",
      cwd: "/root/student-bot",
      interpreter: "python3",
      env: {
        OPS_CONSOLE_HOST: "127.0.0.1",
        OPS_CONSOLE_PORT: "8091",
        OPS_CONSOLE_URL_PREFIX: "/ops-console"
      }
    }
  ]
};
