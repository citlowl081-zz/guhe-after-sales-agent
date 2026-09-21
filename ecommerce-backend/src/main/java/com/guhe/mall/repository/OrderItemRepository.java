package com.guhe.mall.repository;

import com.guhe.mall.entity.OrderEntity;
import com.guhe.mall.entity.OrderItem;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderItemRepository extends JpaRepository<OrderItem, Long> {

    List<OrderItem> findByOrderEntity(OrderEntity orderEntity);
}
