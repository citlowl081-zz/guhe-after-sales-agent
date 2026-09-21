package com.guhe.mall.repository;

import com.guhe.mall.entity.LogisticsInfo;
import com.guhe.mall.entity.OrderEntity;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LogisticsInfoRepository extends JpaRepository<LogisticsInfo, Long> {

    Optional<LogisticsInfo> findByOrderEntity(OrderEntity orderEntity);
}
