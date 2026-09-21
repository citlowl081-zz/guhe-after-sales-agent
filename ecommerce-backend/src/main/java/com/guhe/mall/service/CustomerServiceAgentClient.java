package com.guhe.mall.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.guhe.mall.dto.AgentChatRequest;
import com.guhe.mall.dto.AgentResumeRequest;

public interface CustomerServiceAgentClient {

    JsonNode chat(AgentChatRequest request);

    JsonNode resume(AgentResumeRequest request);
}
