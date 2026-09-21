package com.guhe.mall.repository;

import com.guhe.mall.entity.BalanceTransaction;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface BalanceTransactionRepository extends JpaRepository<BalanceTransaction, Long> {

    List<BalanceTransaction> findByUserIdOrderByCreatedAtDesc(String userId);

    List<BalanceTransaction> findByUserIdAndTypeOrderByCreatedAtDesc(String userId, String type);

    Optional<BalanceTransaction> findByAfterSaleNo(String afterSaleNo);
}
