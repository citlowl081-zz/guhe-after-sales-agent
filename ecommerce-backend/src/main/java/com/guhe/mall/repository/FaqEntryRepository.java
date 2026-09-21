package com.guhe.mall.repository;

import com.guhe.mall.entity.FaqEntry;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FaqEntryRepository extends JpaRepository<FaqEntry, Long> {

    List<FaqEntry> findByCategoryContainingIgnoreCaseOrQuestionContainingIgnoreCaseOrAnswerContainingIgnoreCase(
        String categoryKeyword,
        String questionKeyword,
        String answerKeyword);
}
