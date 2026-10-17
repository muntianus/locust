from locust import HttpUser, task, between

class PerfOpsUser(HttpUser):
    wait_time = between(1, 3)
    @task
    def health(self):
        self.client.get('/api/health')
