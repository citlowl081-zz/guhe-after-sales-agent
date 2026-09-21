package com.guhe.mall.service;

import com.guhe.mall.dto.AfterSalePolicyResponse;
import com.guhe.mall.entity.AfterSalePolicy;
import com.guhe.mall.repository.AfterSalePolicyRepository;
import java.util.List;
import java.util.stream.Collectors;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class AfterSaleService {

    private final AfterSalePolicyRepository afterSalePolicyRepository;

    public AfterSaleService(AfterSalePolicyRepository afterSalePolicyRepository) {
        this.afterSalePolicyRepository = afterSalePolicyRepository;
    }

    public List<AfterSalePolicyResponse> listPolicies(String sceneKey) {
        List<AfterSalePolicy> policies;
        if (StringUtils.hasText(sceneKey)) {
            policies = afterSalePolicyRepository.findBySceneKeyContainingIgnoreCase(sceneKey);
        } else {
            policies = afterSalePolicyRepository.findAll();
        }
        return policies.stream()
            .map(policy -> new AfterSalePolicyResponse(policy.getSceneKey(), policy.getTitle(), policy.getContent(),
                policy.getEligibility(), policy.getApplicableConditions(), policy.getExclusionConditions(),
                policy.getRequiredEvidence(), policy.getRequiresManualReview(), policy.getSuggestedAction(),
                policy.getPolicyVersion()))
            .collect(Collectors.toList());
    }
}
