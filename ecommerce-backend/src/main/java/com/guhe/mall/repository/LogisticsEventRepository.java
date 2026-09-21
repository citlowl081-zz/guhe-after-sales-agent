package com.guhe.mall.repository;

import com.guhe.mall.entity.LogisticsEvent;
import com.guhe.mall.entity.LogisticsInfo;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LogisticsEventRepository extends JpaRepository<LogisticsEvent, Long> {

    List<LogisticsEvent> findByLogisticsInfoOrderByOccurredAtDesc(LogisticsInfo logisticsInfo);
}
