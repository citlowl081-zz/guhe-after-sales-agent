package com.guhe.mall.service;

import com.guhe.mall.dto.FaqResponse;
import com.guhe.mall.entity.FaqEntry;
import com.guhe.mall.repository.FaqEntryRepository;
import java.util.List;
import java.util.stream.Collectors;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class FaqService {

    private final FaqEntryRepository faqEntryRepository;

    public FaqService(FaqEntryRepository faqEntryRepository) {
        this.faqEntryRepository = faqEntryRepository;
    }

    public List<FaqResponse> listFaq(String keyword) {
        List<FaqEntry> entries;
        if (StringUtils.hasText(keyword)) {
            entries = faqEntryRepository.findByCategoryContainingIgnoreCaseOrQuestionContainingIgnoreCaseOrAnswerContainingIgnoreCase(
                keyword,
                keyword,
                keyword);
        } else {
            entries = faqEntryRepository.findAll();
        }
        return entries.stream()
            .map(entry -> new FaqResponse(entry.getCategory(), entry.getQuestion(), entry.getAnswer()))
            .collect(Collectors.toList());
    }
}
